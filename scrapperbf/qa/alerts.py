"""Alertes destinées à l'opérateur humain.

Une alerte n'est utile que si elle dit *où regarder* et *quoi faire*. Chaque
alerte porte donc obligatoirement :

* un ``code`` stable, pour filtrer dans le dashboard et pour mémoriser qu'un
  type d'alerte a déjà été jugé faux positif ;
* des références de page, parce que la vérification se fait dans le PDF ;
* un message en français et une piste d'action.

Les codes sont figés : les retours humains (faux positifs) sont stockés par
code, donc renommer un code revient à perdre cet historique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    INFO = 1       # information de contexte, aucune action attendue
    WARNING = 2    # à vérifier si le temps le permet
    ERROR = 3      # l'annonce est probablement inexploitable en l'état
    CRITICAL = 4   # problème de structure : plusieurs annonces sont en jeu

    @property
    def label(self) -> str:
        return {1: "info", 2: "avertissement", 3: "erreur", 4: "critique"}[int(self)]


class Code:
    """Catalogue figé des codes d'alerte."""

    # --- champs
    CHAMP_CRITIQUE_MANQUANT = "CHAMP_CRITIQUE_MANQUANT"
    CHAMP_IMPORTANT_MANQUANT = "CHAMP_IMPORTANT_MANQUANT"
    CHAMP_INVALIDE = "CHAMP_INVALIDE"
    CHAMP_SUSPECT = "CHAMP_SUSPECT"
    CHAMP_NON_ANCRE = "CHAMP_NON_ANCRE"
    # --- cohérence interne
    LOTS_INCOHERENTS = "LOTS_INCOHERENTS"
    MONTANTS_INCOHERENTS = "MONTANTS_INCOHERENTS"
    DATES_INCOHERENTES = "DATES_INCOHERENTES"
    # --- classement
    TITRE_NON_CLASSE = "TITRE_NON_CLASSE"
    TITRE_ABSENT = "TITRE_ABSENT"
    CLASSEMENT_INCERTAIN = "CLASSEMENT_INCERTAIN"
    SECTION_INCOHERENTE = "SECTION_INCOHERENTE"
    # --- structure du document
    PAGE_DUPLIQUEE = "PAGE_DUPLIQUEE"
    ANNONCE_DUPLIQUEE = "ANNONCE_DUPLIQUEE"
    NUMEROTATION_PAGE_ANORMALE = "NUMEROTATION_PAGE_ANORMALE"
    ANNONCE_TRONQUEE = "ANNONCE_TRONQUEE"
    ANNONCE_MULTIPAGE = "ANNONCE_MULTIPAGE"
    PAGE_ORPHELINE = "PAGE_ORPHELINE"
    PAGE_VIDE = "PAGE_VIDE"
    # --- extraction
    EXTRACTION_ECHOUEE = "EXTRACTION_ECHOUEE"
    OCR_DEGRADE = "OCR_DEGRADE"


@dataclass
class Alert:
    """Un problème détecté, adressé à un humain."""

    code: str
    severity: Severity
    message: str
    page_refs: list[int] = field(default_factory=list)
    unit_id: str | None = None
    field_name: str | None = None
    hint: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def pages_label(self) -> str:
        if not self.page_refs:
            return "page inconnue"
        pages = sorted(set(self.page_refs))
        if len(pages) == 1:
            return f"p. {pages[0]}"
        if pages == list(range(pages[0], pages[-1] + 1)):
            return f"p. {pages[0]}–{pages[-1]}"
        return "p. " + ", ".join(str(p) for p in pages)

    def human(self) -> str:
        """Ligne lisible telle qu'affichée à l'opérateur."""
        parts = [f"[{self.severity.label.upper()}] {self.pages_label} — {self.message}"]
        if self.field_name:
            parts.append(f"(champ « {self.field_name} »)")
        if self.hint:
            parts.append(f"→ {self.hint}")
        return " ".join(parts)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": int(self.severity),
            "severity_label": self.severity.label,
            "message": self.message,
            "pages": sorted(set(self.page_refs)),
            "pages_label": self.pages_label,
            "unit_id": self.unit_id,
            "field": self.field_name,
            "hint": self.hint,
            "evidence": self.evidence,
            "human": self.human(),
        }


def sort_alerts(alerts: list[Alert]) -> list[Alert]:
    """Gravité décroissante, puis par page : l'ordre de traitement humain."""
    return sorted(
        alerts,
        key=lambda a: (-int(a.severity), min(a.page_refs) if a.page_refs else 10**6, a.code),
    )


def summarize(alerts: list[Alert]) -> dict[str, int]:
    """Compte par gravité, pour l'en-tête du dashboard."""
    counts = {s.label: 0 for s in Severity}
    for a in alerts:
        counts[a.severity.label] += 1
    counts["total"] = len(alerts)
    return counts
