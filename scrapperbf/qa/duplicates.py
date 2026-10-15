"""Détection des doubles : pages dupliquées et annonces republiées.

Deux phénomènes distincts, souvent confondus :

* **page dupliquée** — accident de numérisation (même feuille scannée deux
  fois, PDF concaténé en double). Symptôme : deux pages au contenu quasi
  identique. Conséquence si on l'ignore : chaque annonce de la page est
  extraite deux fois et facturée deux fois au LLM.
* **annonce dupliquée** — la même annonce paraît dans deux pages différentes,
  parfois légitimement (republication, rectificatif) et parfois par erreur de
  segmentation. Le remède n'est pas le même, donc on les sépare.

La comparaison se fait sur des n-grammes de tokens normalisés : insensible aux
accents, à la casse et aux micro-variations d'OCR, contrairement à une égalité
de chaînes qui ne détecterait presque rien.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from ..core.pages import Document, Page
from ..core.text import jaccard, normalize, preview, shingles
from .alerts import Alert, Code, Severity

EXACT_THRESHOLD = 0.98
NEAR_THRESHOLD = 0.88
UNIT_THRESHOLD = 0.85
MIN_TOKENS_FOR_COMPARISON = 40  # sous ce seuil, la similarité n'est pas fiable


@dataclass
class DuplicateGroup:
    """Ensemble de pages ou d'annonces jugées identiques."""

    members: list[str]           # identifiants lisibles (p. 12, AVIS#3...)
    pages: list[int]
    similarity: float
    kind: str                    # "exacte" | "quasi-identique"
    sample: str = ""

    def to_dict(self) -> dict:
        return {
            "members": self.members,
            "pages": self.pages,
            "similarity": round(self.similarity, 4),
            "kind": self.kind,
            "sample": self.sample,
        }


def _page_label(page: Page) -> str:
    """Étiquette non ambiguë : deux pages peuvent porter le même numéro
    imprimé (c'est précisément le cas qu'on cherche), donc l'ordinal de
    parsing est ajouté pour que l'humain sache quelle feuille ouvrir."""
    return f"p. {page.page_number} (feuille {page.ordinal})"


def _page_fingerprint(page: Page) -> set:
    return shingles(page.raw_text, size=5)


def find_duplicate_pages(doc: Document, near_threshold: float = NEAR_THRESHOLD) -> list[DuplicateGroup]:
    """Compare chaque paire de pages du document."""
    candidates = [
        (p, _page_fingerprint(p))
        for p in doc.pages
        if len(normalize(p.raw_text).split()) >= MIN_TOKENS_FOR_COMPARISON
    ]
    groups: list[DuplicateGroup] = []
    for (p1, f1), (p2, f2) in combinations(candidates, 2):
        sim = jaccard(f1, f2)
        if sim >= near_threshold:
            groups.append(
                DuplicateGroup(
                    members=[_page_label(p1), _page_label(p2)],
                    pages=[p1.page_number, p2.page_number],
                    similarity=sim,
                    kind="exacte" if sim >= EXACT_THRESHOLD else "quasi-identique",
                    sample=preview(p2.raw_text, 120),
                )
            )
    return sorted(groups, key=lambda g: -g.similarity)


def find_duplicate_units(units: list[dict], threshold: float = UNIT_THRESHOLD) -> list[DuplicateGroup]:
    """Compare les annonces segmentées entre elles.

    ``units`` : dicts portant au minimum ``id``, ``flat_text``, ``page_debut``.
    """
    prepared = []
    for u in units:
        text = u.get("flat_text", "")
        if len(normalize(text).split()) < MIN_TOKENS_FOR_COMPARISON:
            continue
        prepared.append((u, shingles(text, size=5)))

    groups: list[DuplicateGroup] = []
    for (u1, f1), (u2, f2) in combinations(prepared, 2):
        sim = jaccard(f1, f2)
        if sim >= threshold:
            pages = sorted({u1.get("page_debut"), u2.get("page_debut")} - {None})
            groups.append(
                DuplicateGroup(
                    members=[str(u1.get("id")), str(u2.get("id"))],
                    pages=pages,
                    similarity=sim,
                    kind="exacte" if sim >= EXACT_THRESHOLD else "quasi-identique",
                    sample=preview(u2.get("flat_text", ""), 120),
                )
            )
    return sorted(groups, key=lambda g: -g.similarity)


@dataclass
class PaginationAnomaly:
    """Rupture dans la numérotation imprimée des pages."""

    kind: str          # "repetition" | "saut" | "recul" | "non_numerotee"
    pages: list[int]
    detail: str
    ordinals: list[int] = field(default_factory=list)


def check_pagination(doc: Document) -> list[PaginationAnomaly]:
    """Vérifie que les numéros imprimés se suivent.

    Un numéro répété signale typiquement une page scannée deux fois ; un saut
    signale une page manquante dans l'export PDF — dans les deux cas, des
    annonces sont perdues ou dupliquées en aval.
    """
    anomalies: list[PaginationAnomaly] = []
    numbered = [p for p in doc.pages if p.declared_number is not None]

    unnumbered = [p for p in doc.pages if p.declared_number is None and p.content_chars > 200]
    if unnumbered:
        anomalies.append(
            PaginationAnomaly(
                kind="non_numerotee",
                pages=[p.page_number for p in unnumbered],
                detail=f"{len(unnumbered)} page(s) substantielle(s) sans numéro de pied de page détecté",
                ordinals=[p.ordinal for p in unnumbered],
            )
        )

    for prev, cur in zip(numbered, numbered[1:]):
        delta = cur.declared_number - prev.declared_number
        if delta == 1:
            continue
        if delta == 0:
            kind, detail = "repetition", f"le numéro {cur.declared_number} apparaît deux fois de suite"
        elif delta < 0:
            kind, detail = "recul", f"la numérotation recule : {prev.declared_number} → {cur.declared_number}"
        else:
            kind, detail = "saut", f"{delta - 1} page(s) manquante(s) entre {prev.declared_number} et {cur.declared_number}"
        anomalies.append(
            PaginationAnomaly(
                kind=kind,
                pages=[prev.declared_number, cur.declared_number],
                detail=detail,
                ordinals=[prev.ordinal, cur.ordinal],
            )
        )
    return anomalies


def duplicate_alerts(
    page_groups: list[DuplicateGroup],
    unit_groups: list[DuplicateGroup],
    anomalies: list[PaginationAnomaly],
) -> list[Alert]:
    """Traduit les constats en alertes adressées à l'opérateur."""
    alerts: list[Alert] = []

    for g in page_groups:
        alerts.append(
            Alert(
                code=Code.PAGE_DUPLIQUEE,
                severity=Severity.CRITICAL if g.kind == "exacte" else Severity.ERROR,
                message=f"Pages {' et '.join(g.members)} {g.kind}s (similarité {g.similarity:.0%})",
                page_refs=g.pages,
                hint="Vérifier le PDF source : si la page est scannée deux fois, "
                     "l'écarter avant extraction évite de payer deux fois le LLM.",
                evidence=[g.sample],
            )
        )

    for g in unit_groups:
        alerts.append(
            Alert(
                code=Code.ANNONCE_DUPLIQUEE,
                severity=Severity.WARNING,
                message=f"Annonces {' et '.join(g.members)} {g.kind}s (similarité {g.similarity:.0%})",
                page_refs=g.pages,
                hint="Republication légitime (rectificatif) ou découpage en double : "
                     "confirmer puis ne garder qu'une occurrence.",
                evidence=[g.sample],
            )
        )

    for a in anomalies:
        severity = Severity.ERROR if a.kind in ("repetition", "saut") else Severity.WARNING
        alerts.append(
            Alert(
                code=Code.NUMEROTATION_PAGE_ANORMALE,
                severity=severity,
                message=f"Numérotation anormale ({a.kind}) : {a.detail}",
                page_refs=a.pages,
                hint="Un numéro répété ou manquant signale une page en double ou absente du PDF.",
            )
        )
    return alerts
