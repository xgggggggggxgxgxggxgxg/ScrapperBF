"""Segmentation des annonces, pilotée par les titres.

Reprend le principe de ``new_logic/layer2_segment.py`` — découper le flux en
unités métier sans dépendre d'un vocabulaire figé — avec trois différences qui
comptent pour le contrôle qualité :

1. les blocs portent leur numéro de page, donc chaque annonce connaît ses pages
   réelles de début et de fin (indispensable pour alerter un humain) ;
2. la nature de l'annonce est décidée par ``core.titles``, pas devinée après
   coup : c'est elle qui détermine ensuite les champs attendus ;
3. la décision de couper est tracée (``split_reason``), pour qu'un découpage
   douteux soit visible dans le dashboard au lieu d'être subi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pages import Block, Document
from .text import normalize, preview
from .titles import TitleAnalysis, TitleKind, analyze_title

ATTRIBUTAIRE_RE = re.compile(r"attributaire")
# Sous ce volume, une « annonce » n'est qu'un reliquat de mise en page
# (bandeau, titre orphelin) : elle est recollée à la suivante.
MIN_CONTENT_CHARS = 60


@dataclass
class Notice:
    """Une annonce : son titre, sa nature, ses pages, son contenu."""

    id: str
    section: str                  # "AVIS" | "RESULTATS"
    kind: TitleKind
    kind_confidence: float
    page_debut: int
    page_fin: int
    title: str = ""
    inherited_authority: str | None = None
    category: str | None = None        # MINISTERES | COLLECTIVITES
    marche_type: str | None = None
    blocks: list[Block] = field(default_factory=list)
    split_reason: str = ""
    title_evidence: list[str] = field(default_factory=list)

    @property
    def pages(self) -> list[int]:
        return list(range(self.page_debut, self.page_fin + 1))

    @property
    def content_chars(self) -> int:
        return sum(len(b.text) for b in self.blocks if b.type in ("text", "table"))

    @property
    def flat_text(self) -> str:
        """Texte aplati, tel qu'envoyé au LLM d'extraction."""
        parts = []
        if self.inherited_authority:
            parts.append(f"# {self.inherited_authority} (entité héritée du bloc précédent)")
        for b in self.blocks:
            if b.type == "title":
                parts.append(f"# {b.text}")
            elif b.type == "table":
                parts.append("[TABLEAU]")
                parts.extend(" | ".join(row) for row in b.matrix)
            else:
                parts.append(b.text)
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "section": self.section,
            "kind": self.kind.value,
            "kind_confidence": round(self.kind_confidence, 3),
            "title": self.title,
            "inherited_authority": self.inherited_authority,
            "category": self.category,
            "marche_type": self.marche_type,
            "page_debut": self.page_debut,
            "page_fin": self.page_fin,
            "pages": self.pages,
            "content_chars": self.content_chars,
            "split_reason": self.split_reason,
            "title_evidence": self.title_evidence,
            "flat_text": self.flat_text,
            "preview": preview(self.flat_text, 200),
        }


class _Builder:
    """État de la segmentation en cours de parcours du document."""

    def __init__(self) -> None:
        self.notices: list[Notice] = []
        self.current: Notice | None = None
        self.category: str | None = None
        self.marche_type: str | None = None
        self.content_seen = False
        self.awaiting_new_unit = True
        self.pending_authority: str | None = None
        self.counter = 0

    def start(self, section: str, analysis: TitleAnalysis, block: Block, reason: str) -> None:
        self.flush()
        self.counter += 1
        self.current = Notice(
            id=f"{section}#{self.counter}",
            section=section,
            kind=analysis.kind if analysis.starts_notice else TitleKind.UNKNOWN,
            kind_confidence=analysis.confidence if analysis.starts_notice else 0.2,
            page_debut=block.page_number,
            page_fin=block.page_number,
            title=block.text if analysis.starts_notice else "",
            inherited_authority=self.pending_authority,
            category=self.category,
            marche_type=analysis.marche_type or self.marche_type,
            split_reason=reason,
            title_evidence=list(analysis.evidence),
        )
        self.awaiting_new_unit = False
        self.content_seen = False

    def ensure(self, section: str, block: Block) -> None:
        """Ouvre une unité implicite pour du contenu arrivé sans titre."""
        if self.current is None:
            self.counter += 1
            self.current = Notice(
                id=f"{section}#{self.counter}",
                section=section,
                kind=TitleKind.UNKNOWN,
                kind_confidence=0.2,
                page_debut=block.page_number,
                page_fin=block.page_number,
                inherited_authority=self.pending_authority,
                category=self.category,
                marche_type=self.marche_type,
                split_reason="contenu rencontré sans titre d'annonce préalable",
            )

    def add(self, block: Block) -> None:
        assert self.current is not None
        self.current.blocks.append(block)
        self.current.page_fin = max(self.current.page_fin, block.page_number)

    def flush(self) -> None:
        if self.current and self.current.blocks:
            self.notices.append(self.current)
        self.current = None
        self.content_seen = False


def segment(doc: Document, skip_toc: bool = True) -> list[Notice]:
    """Découpe le document en annonces.

    Déclencheurs d'une nouvelle annonce :

    * un titre dont ``core.titles`` dit qu'il porte une annonce, dès lors que
      du contenu a déjà rejoint l'annonce en cours (sinon on fragmenterait un
      avis qui empile « AVIS DE DEMANDE DE PRIX » puis son objet) ;
    * un marqueur de section (« Travaux », « Fournitures... »), qui sépare
      toujours deux annonces dans la mise en page du bulletin ;
    * pour les résultats, la ligne « ATTRIBUTAIRE » qui clôt l'unité.
    """
    builder = _Builder()
    section = "AVIS"

    for page in doc.pages:
        if skip_toc and page.is_toc:
            continue

        for block in page.blocks:
            if block.type == "title":
                analysis = analyze_title(block.text)

                if analysis.kind is TitleKind.CATEGORY_MARKER:
                    builder.category = analysis.category
                    # Le type de marché ne survit pas à un changement de
                    # catégorie : le bulletin recommence son cycle de sections.
                    builder.marche_type = None
                    builder.pending_authority = None
                    continue

                if analysis.kind is TitleKind.SECTION_MARKER:
                    builder.marche_type = analysis.marche_type
                    if builder.content_seen:
                        builder.start(section, analysis, block,
                                      f"marqueur de section « {analysis.marker_value} »")
                    continue

                if analysis.kind is TitleKind.RESULTATS:
                    section = "RESULTATS"
                elif analysis.section == "AVIS" and analysis.starts_notice:
                    section = "AVIS"

                if analysis.kind is TitleKind.AUTORITE:
                    # Un en-tête d'institution n'ouvre pas d'annonce : il sera
                    # hérité par les annonces qui suivent et qui ne le répètent pas.
                    builder.pending_authority = block.text
                    builder.ensure(section, block)
                    builder.add(block)
                    continue

                starts = analysis.starts_notice and (builder.content_seen or builder.awaiting_new_unit)
                if starts:
                    builder.start(section, analysis, block,
                                  f"titre d'annonce reconnu ({analysis.kind.value})")
                else:
                    builder.ensure(section, block)
                    if builder.current and not builder.current.title:
                        # Le titre renseigne une unité ouverte sans titre — y
                        # compris un titre non classé : le conserver permet de
                        # le signaler à l'opérateur (TITRE_NON_CLASSE) au lieu
                        # de produire une annonce sans titre du tout.
                        builder.current.title = block.text
                        builder.current.kind = analysis.kind
                        builder.current.kind_confidence = analysis.confidence
                        builder.current.title_evidence = list(analysis.evidence)
                    if analysis.marche_type and builder.current and not builder.current.marche_type:
                        builder.current.marche_type = analysis.marche_type
                builder.add(block)
                continue

            builder.ensure(section, block)
            builder.add(block)
            builder.content_seen = True

            if section == "RESULTATS" and ATTRIBUTAIRE_RE.search(normalize(block.text)):
                builder.flush()
                builder.awaiting_new_unit = True

    builder.flush()
    return _merge_fragments(builder.notices)


def _merge_fragments(notices: list[Notice]) -> list[Notice]:
    """Recolle les unités trop maigres pour être de vraies annonces.

    Un fragment est rattaché à l'annonce *suivante* : dans la mise en page du
    bulletin, un titre isolé en bas de colonne annonce ce qui vient après.
    """
    merged: list[Notice] = []
    pending: list[Block] = []
    pending_pages: list[int] = []

    for notice in notices:
        if notice.content_chars < MIN_CONTENT_CHARS:
            pending.extend(notice.blocks)
            pending_pages.extend(notice.pages)
            continue
        if pending:
            notice.blocks = pending + notice.blocks
            notice.page_debut = min([notice.page_debut, *pending_pages])
            notice.split_reason += " ; fragment(s) de titre recollé(s)"
            pending, pending_pages = [], []
        merged.append(notice)

    if pending and merged:
        # Fragment en fin de document : il appartient à la dernière annonce.
        merged[-1].blocks.extend(pending)
        merged[-1].page_fin = max([merged[-1].page_fin, *pending_pages])

    for index, notice in enumerate(merged, start=1):
        notice.id = f"{notice.section}#{index}"
    return merged
