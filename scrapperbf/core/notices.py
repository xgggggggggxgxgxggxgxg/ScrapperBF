"""Annonces complètes : en-tête de maquette + corps de texte.

``core.layout`` sait où commencent les annonces ; il reste à leur donner leur
contenu. Le découpage suit la maquette plutôt que le texte :

* le corps d'une annonce commence **sous la dernière bande colorée** de la page
  où elle s'ouvre ;
* il se poursuit sur les pages suivantes **jusqu'à la prochaine bande jaune**,
  car le bulletin ne répète aucun en-tête quand une annonce déborde ;
* les lignes de bandeau (numéro du quotidien, adresses du site, numéro de page)
  sont retirées : elles appartiennent à la mise en page, pas à l'annonce, et
  leurs dates et numéros se font passer pour des valeurs de l'annonce.

C'est ce dernier point qui compte le plus en pratique : sans ce nettoyage, la
date de parution du bulletin et le numéro d'un autre quotidien deviennent des
candidats sérieux pour « date limite de dépôt » et « numéro de référence ».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from .layout import Band, LayoutBackend, Role, bands_on_page, canonical_category
from .taxonomy import Classification, classify
from .text import preview

# Lignes de bandeau, à retirer du corps des annonces.
BOILERPLATE_RES = [
    re.compile(r"^N°\s*\d{3,5}\s*[-–—]\s*\w+\s+\d{1,2}\s+\w+\s+\d{4}", re.IGNORECASE),
    # Le pied de page porte deux adresses sur la même ligne : une ligne faite
    # uniquement d'adresses n'appartient pas à l'annonce.
    re.compile(r"^(?:(?:https?://)?www\.[\w.-]+\s*)+$", re.IGNORECASE),
    re.compile(r"Quotidien d.information de la Direction", re.IGNORECASE),
    re.compile(r"^Marchés Publics et des Engagements Financiers", re.IGNORECASE),
    re.compile(r"^\d{1,4}$"),                       # numéro de page isolé
    re.compile(r"^N°\s*\d{3,5}\s*\(?bis\)?\s*$", re.IGNORECASE),
]
# Bandeau de pied de page accolé à la fin d'une ligne utile. Ancré sur la fin
# de ligne : un rectificatif cite légitimement « du Quotidien N°4502 – Lundi 05
# octobre 2026, page 49 » en milieu de phrase, et ce numéro-là identifie l'avis
# corrigé — le retirer rendrait le rectificatif inexploitable.
TRAILING_BANNER_RE = re.compile(
    r"\s*N°\s*\d{3,5}\s*[-–—]\s*(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)?\s*"
    r"\d{1,2}\s+\w+\s+\d{4}\s*$",
    re.IGNORECASE,
)


class NoticeBackend(LayoutBackend, Protocol):
    """Ce que ``core.layout`` demande, plus les lignes de texte positionnées."""

    def page_text_lines(self, path: str, index: int) -> list[tuple[float, str]]: ...


@dataclass
class Notice:
    """Une annonce du bulletin, prête à être extraite et classée."""

    id: str
    document: str
    page_debut: int
    page_fin: int
    emetteur: str
    categorie: str | None
    objet: str
    portee: str
    body_lines: list[str] = field(default_factory=list)
    classification: Classification | None = None

    @property
    def body(self) -> str:
        return "\n".join(self.body_lines)

    @property
    def pages(self) -> list[int]:
        return list(range(self.page_debut, self.page_fin + 1))

    @property
    def categorie_canonique(self) -> str | None:
        return canonical_category(self.categorie)

    @property
    def header(self) -> dict[str, object]:
        """Ce que la maquette a établi, à donner au modèle comme un acquis."""
        return {
            "autorite_emettrice": self.emetteur,
            "objet": self.objet,
            "categorie": self.categorie_canonique,
        }

    def to_dict(self) -> dict:
        base = {
            "id": self.id,
            "document": self.document,
            "page_debut": self.page_debut,
            "page_fin": self.page_fin,
            "pages": self.pages,
            "emetteur": self.emetteur,
            "categorie": self.categorie_canonique,
            "objet": self.objet,
            "portee": self.portee,
            "body_chars": len(self.body),
            "preview": preview(self.body, 240),
        }
        if self.classification:
            base.update(self.classification.to_dict())
        return base


def clean_body_lines(lignes: list[str], bandes: set[str]) -> list[str]:
    """Retire les lignes de bandeau et les textes déjà portés par les bandes."""
    gardees: list[str] = []
    for ligne in lignes:
        nue = ligne.strip()
        if not nue or nue in bandes:
            continue
        if any(r.search(nue) for r in BOILERPLATE_RES):
            continue
        # Le bandeau se glisse parfois en fin de ligne utile : on l'y coupe.
        nue = TRAILING_BANNER_RE.sub("", nue).strip()
        if nue:
            gardees.append(nue)
    return gardees


def extract_notices(
    path: str,
    backend: NoticeBackend,
    *,
    document: str | None = None,
    with_classification: bool = True,
) -> list[Notice]:
    """Découpe un PDF en annonces complètes, en-tête et corps."""
    nom = document or path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    annonces: list[Notice] = []
    categorie: str | None = None
    courante: Notice | None = None
    precedente: Band | None = None

    for index in range(backend.page_count(path)):
        page = index + 1
        bandes = bands_on_page(
            backend.page_fills(path, index), backend.page_words(path, index), page
        )
        lignes = backend.page_text_lines(path, index)
        textes_bandes = {b.text for b in bandes}

        for bande in bandes:
            if bande.role is Role.CATEGORIE:
                categorie = bande.text
            elif bande.role is Role.EMETTEUR:
                if (
                    courante is not None
                    and precedente is not None
                    and precedente.role is Role.EMETTEUR
                    and precedente.page == page
                    and bande.y0 - precedente.y1 <= 2.0
                ):
                    # Seconde ligne du même émetteur, pas une nouvelle annonce.
                    courante.emetteur = f"{courante.emetteur} {bande.text}".strip()
                else:
                    courante = Notice(
                        id=f"{nom}#{len(annonces) + 1}",
                        document=nom,
                        page_debut=page,
                        page_fin=page,
                        emetteur=bande.text,
                        categorie=categorie,
                        objet="",
                        portee="",
                    )
                    annonces.append(courante)
            elif bande.role is Role.OBJET and courante is not None:
                courante.objet = f"{courante.objet} {bande.text}".strip()
            precedente = bande

        if courante is None:
            continue

        # Le corps est ce qui suit la dernière bande de la page. Sur une page de
        # continuation, il n'y a pas de bande : tout le texte appartient à
        # l'annonce ouverte.
        seuil = max((b.y1 for b in bandes), default=0.0)
        suite = [t for y, t in sorted(lignes) if y > seuil]
        courante.body_lines.extend(clean_body_lines(suite, textes_bandes))
        courante.page_fin = page

    if with_classification:
        for annonce in annonces:
            annonce.classification = classify(
                annonce.objet, annonce.emetteur, annonce.categorie_canonique, annonce.body
            )
            annonce.portee = _portee(annonce)
    return annonces


def _portee(annonce: Notice) -> str:
    """« NATIONAL » (appel d'offres simple) ou « REGIONAL » (collectivité)."""
    from .layout import NoticeHeader

    return NoticeHeader(emetteur=annonce.emetteur, categorie=annonce.categorie).portee


def default_backend():
    """Backend PyMuPDF complet, ou ``None`` si la bibliothèque est absente."""
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None

    class _PyMuPdfNotices:
        def page_count(self, path: str) -> int:
            with pymupdf.open(path) as doc:
                return doc.page_count

        def page_fills(self, path: str, index: int) -> list[dict]:
            with pymupdf.open(path) as doc:
                out = []
                for d in doc[index].get_drawings():
                    if d.get("fill") is None:
                        continue
                    r = d["rect"]
                    out.append({"color": tuple(d["fill"]), "rect": (r.x0, r.y0, r.x1, r.y1)})
                return out

        def page_words(self, path: str, index: int) -> list[tuple]:
            with pymupdf.open(path) as doc:
                return doc[index].get_text("words")

        def page_text_lines(self, path: str, index: int) -> list[tuple[float, str]]:
            with pymupdf.open(path) as doc:
                data = doc[index].get_text("dict")
            lignes: list[tuple[float, str]] = []
            for bloc in data.get("blocks", []):
                for ligne in bloc.get("lines", []):
                    texte = "".join(s.get("text", "") for s in ligne.get("spans", []))
                    if texte.strip():
                        lignes.append((ligne["bbox"][1], texte.strip()))
            return lignes

    return _PyMuPdfNotices()
