"""Continuité des annonces entre pages.

Le bulletin ne répète pas d'en-tête quand une annonce déborde sur la page
suivante : rien, dans le texte de la page 35, ne dit qu'elle poursuit l'avis
commencé page 34. C'est la principale source de données fausses du pipeline :

* une annonce coupée, extraite sur sa seule première page, perd ses dates et
  son signataire — et le score de complétude accuse à tort le modèle ;
* une page de continuation prise pour une annonce autonome crée une annonce
  fantôme, sans autorité ni objet.

On raisonne donc par *signaux de bord de page*, jamais sur un mot-clé unique :
présence d'un bloc de signature en fin de page, phrase tronquée, numérotation
réglementaire (1., 2., 3...) qui se poursuit, ouverture par un titre d'annonce.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.pages import Document, Page
from ..core.text import normalize, preview
from ..core.titles import TitleKind, analyze_title
from .alerts import Alert, Code, Severity

# Formules de clôture d'un avis : le document les place en fin d'annonce.
SIGNATURE_RE = re.compile(
    r"president(e)?\s+de\s+la\s+commission|le\s+directeur\s+general|la\s+directrice\s+generale"
    r"|personne\s+responsable\s+des\s+marches|\bprm\b|le\s+(maire|president|secretaire\s+general)"
    r"|fait\s+a\s+\w+|le\s+ministre|chevalier\s+de\s+l.ordre|officier\s+de\s+l.ordre"
)
NUMBERED_ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+\S", re.MULTILINE)
# Une page qui finit sans ponctuation forte a été coupée en pleine phrase.
STRONG_END_CHARS = ".!?:»”\")"
MIN_PAGE_CHARS = 80  # en dessous : page quasi vide, signalée à part


@dataclass
class PageSignals:
    """Indices lus aux bords d'une page."""

    page_number: int
    ordinal: int
    starts_notice: bool
    start_kind: TitleKind | None
    ends_with_signature: bool
    ends_truncated: bool
    numbered_items: list[int] = field(default_factory=list)
    is_empty: bool = False
    is_toc: bool = False
    head: str = ""
    tail: str = ""

    def to_dict(self) -> dict:
        return {
            "page": self.page_number,
            "ordinal": self.ordinal,
            "starts_notice": self.starts_notice,
            "start_kind": self.start_kind.value if self.start_kind else None,
            "ends_with_signature": self.ends_with_signature,
            "ends_truncated": self.ends_truncated,
            "numbered_items": self.numbered_items,
            "is_empty": self.is_empty,
            "is_toc": self.is_toc,
        }


def analyze_page_signals(page: Page) -> PageSignals:
    text = page.raw_text.strip()
    head, tail = text[:500], text[-500:]

    start_kind: TitleKind | None = None
    # On ne consulte que les premiers titres : un titre d'annonce trouvé au
    # milieu de la page ne dit rien sur la façon dont la page *commence*.
    for block in page.blocks[:4]:
        if block.type == "title":
            analysis = analyze_title(block.text)
            if analysis.starts_notice and not analysis.is_structural:
                start_kind = analysis.kind
                break
            if analysis.is_structural:
                # Un marqueur de section ouvre bien une nouvelle annonce.
                start_kind = TitleKind.OBJET
                break

    stripped = text.rstrip()
    return PageSignals(
        page_number=page.page_number,
        ordinal=page.ordinal,
        starts_notice=start_kind is not None,
        start_kind=start_kind,
        ends_with_signature=bool(SIGNATURE_RE.search(normalize(tail))),
        ends_truncated=bool(stripped) and stripped[-1] not in STRONG_END_CHARS,
        numbered_items=[int(n) for n in NUMBERED_ITEM_RE.findall(text)],
        is_empty=page.content_chars < MIN_PAGE_CHARS,
        is_toc=page.is_toc,
        head=preview(head, 120),
        tail=preview(tail, 120),
    )


@dataclass
class NoticeSpan:
    """Suite de pages consécutives attribuées à une même annonce."""

    pages: list[int]
    confidence: float
    reasons: list[str] = field(default_factory=list)
    uncertain: bool = False

    @property
    def is_multipage(self) -> bool:
        return len(self.pages) > 1

    def to_dict(self) -> dict:
        return {
            "pages": self.pages,
            "confidence": round(self.confidence, 3),
            "reasons": self.reasons,
            "uncertain": self.uncertain,
            "multipage": self.is_multipage,
        }


def _continuation_signals(prev: PageSignals, cur: PageSignals) -> list[str]:
    """Raisons de rattacher ``cur`` à l'annonce de ``prev``."""
    signals = []
    if not prev.ends_with_signature:
        signals.append(f"p. {prev.page_number} ne se termine pas par un bloc de signature")
    if prev.ends_truncated:
        signals.append(f"p. {prev.page_number} est coupée en pleine phrase")
    if prev.numbered_items and cur.numbered_items:
        prev_max, cur_min = max(prev.numbered_items), min(cur.numbered_items)
        if cur_min == prev_max + 1:
            signals.append(f"numérotation continue ({prev_max} → {cur_min})")
        elif cur_min <= prev_max:
            # La numérotation repart de 1 : c'est au contraire une rupture.
            signals.append("RUPTURE: la numérotation repart en arrière")
    return signals


def group_pages(signals: list[PageSignals]) -> list[NoticeSpan]:
    """Chaîne les pages en annonces.

    Doctrine : en cas de doute, **sous-fusionner** plutôt que sur-fusionner.
    Fusionner à tort mélange deux annonces en une seule extraction (dégât
    silencieux et difficile à repérer) ; séparer à tort produit une annonce
    incomplète, que le score de complétude signalera de lui-même.
    """
    spans: list[NoticeSpan] = []
    current: NoticeSpan | None = None

    for i, sig in enumerate(signals):
        if sig.is_toc:
            continue
        if current is None or sig.starts_notice:
            if current:
                spans.append(current)
            current = NoticeSpan(pages=[sig.page_number], confidence=0.9 if sig.starts_notice else 0.5,
                                 reasons=["ouvre par un titre d'annonce"] if sig.starts_notice else ["première page traitée"])
            continue

        prev = signals[i - 1]
        reasons = _continuation_signals(prev, sig)
        positive = [r for r in reasons if not r.startswith("RUPTURE")]
        rupture = any(r.startswith("RUPTURE") for r in reasons)

        if positive and not rupture:
            current.pages.append(sig.page_number)
            current.reasons.extend(positive)
            # Deux signaux concordants valent mieux qu'un : la confiance suit.
            current.confidence = min(0.95, 0.55 + 0.2 * len(positive))
        else:
            spans.append(current)
            current = NoticeSpan(
                pages=[sig.page_number],
                confidence=0.4,
                reasons=["aucun titre d'annonce détecté et aucun signal net de continuation"],
                uncertain=True,
            )

    if current:
        spans.append(current)
    return spans


def multipage_alerts(signals: list[PageSignals], spans: list[NoticeSpan]) -> list[Alert]:
    alerts: list[Alert] = []
    by_page = {s.page_number: s for s in signals}

    for sig in signals:
        if sig.is_empty and not sig.is_toc:
            alerts.append(
                Alert(
                    code=Code.PAGE_VIDE,
                    severity=Severity.WARNING,
                    message=f"Page quasi vide ({sig.page_number}) : aucun contenu exploitable",
                    page_refs=[sig.page_number],
                    hint="Page blanche, ou texte non reconnu par l'OCR : contrôler l'image source.",
                )
            )

    for span in spans:
        if span.is_multipage:
            last = by_page.get(span.pages[-1])
            truncated = last is not None and not last.ends_with_signature
            alerts.append(
                Alert(
                    code=Code.ANNONCE_MULTIPAGE,
                    severity=Severity.INFO if not truncated else Severity.WARNING,
                    message=f"Annonce étalée sur {len(span.pages)} pages ({', '.join(map(str, span.pages))})",
                    page_refs=span.pages,
                    hint="Vérifier que l'extraction couvre bien toutes ces pages."
                         + (" La dernière page n'a pas de signature : l'annonce continue peut-être encore."
                            if truncated else ""),
                    evidence=span.reasons[:4],
                )
            )
        if span.uncertain:
            alerts.append(
                Alert(
                    code=Code.PAGE_ORPHELINE,
                    severity=Severity.ERROR,
                    message=f"Page {span.pages[0]} rattachable à aucune annonce de façon fiable",
                    page_refs=span.pages,
                    hint="Décider manuellement : suite de la page précédente, ou annonce dont "
                         "le titre n'a pas été reconnu ?",
                    evidence=span.reasons,
                )
            )

    for sig in signals:
        if sig.ends_truncated and not sig.ends_with_signature and not sig.is_empty:
            spans_with_page = [s for s in spans if sig.page_number in s.pages]
            is_last_of_span = any(s.pages[-1] == sig.page_number for s in spans_with_page)
            if is_last_of_span:
                alerts.append(
                    Alert(
                        code=Code.ANNONCE_TRONQUEE,
                        severity=Severity.ERROR,
                        message=f"Annonce probablement tronquée en fin de page {sig.page_number} "
                                "(ni signature, ni ponctuation finale)",
                        page_refs=[sig.page_number],
                        hint="La suite est peut-être sur la page suivante sans qu'aucun signal ne la relie : "
                             "vérifier dans le PDF.",
                        evidence=[sig.tail],
                    )
                )
    return alerts


def analyze_document(doc: Document) -> tuple[list[PageSignals], list[NoticeSpan], list[Alert]]:
    signals = [analyze_page_signals(p) for p in doc.pages]
    spans = group_pages(signals)
    return signals, spans, multipage_alerts(signals, spans)
