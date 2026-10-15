"""Intégrité de la page, **avant** toute extraction.

Extraire d'abord et contrôler ensuite coûte deux fois : des jetons dépensés sur
une annonce mal découpée, et un diagnostic qui accuse le modèle alors que le
défaut est dans la page. On contrôle donc d'abord, et ce contrôle est gratuit.

Quatre atteintes à l'intégrité rendent une extraction douteuse :

* **annonce à cheval sur plusieurs pages** — extraire la seule première page
  perd dates et signataire, et le score accuse à tort le modèle ;
* **plusieurs annonces sur une même page** — le texte de la voisine contamine
  l'extraction, qui mélange deux marchés ;
* **tableaux dans le corps** — leurs cellules ressemblent à des valeurs et
  polluent les métadonnées (c'est ainsi qu'on obtient « réservée aux 1,00 4 ») ;
* **page dupliquée** — la même annonce sera extraite et facturée deux fois.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.pages import Document
from .alerts import Alert, Code, Severity

# Un corps d'annonce dont une grande part vient de tableaux ne se lit pas en
# phrases : les motifs d'extraction y capturent des cellules.
TABLE_CHARS_RATIO_ALERT = 0.35
MIN_BODY_CHARS = 200


@dataclass
class IntegrityReport:
    """Ce que la page laisse espérer d'une extraction."""

    unit_id: str
    pages: list[int]
    spans_multiple_pages: bool = False
    notices_on_same_page: int = 1
    table_ratio: float = 0.0
    truncated: bool = False
    duplicated_pages: list[int] = field(default_factory=list)
    body_chars: int = 0
    alerts: list[Alert] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        out = []
        if self.spans_multiple_pages:
            out.append(f"annonce étalée sur {len(self.pages)} pages")
        if self.notices_on_same_page > 1:
            out.append(f"{self.notices_on_same_page} annonces sur la même page")
        if self.table_ratio >= TABLE_CHARS_RATIO_ALERT:
            out.append(f"{self.table_ratio:.0%} du corps est en tableaux")
        if self.truncated:
            out.append("corps probablement tronqué")
        if self.duplicated_pages:
            out.append(f"page(s) dupliquée(s) : {self.duplicated_pages}")
        if self.body_chars < MIN_BODY_CHARS:
            out.append(f"corps très court ({self.body_chars} caractères)")
        return out

    @property
    def safe(self) -> bool:
        """Vrai si rien n'empêche d'extraire en confiance.

        Une annonce multi-pages n'est pas un défaut en soi dès lors que le
        corps couvre bien toutes ses pages : c'est pourquoi elle ne suffit pas
        à rendre la page douteuse, contrairement aux trois autres atteintes.
        """
        return not (
            self.notices_on_same_page > 1
            or self.table_ratio >= TABLE_CHARS_RATIO_ALERT
            or self.truncated
            or self.duplicated_pages
            or self.body_chars < MIN_BODY_CHARS
        )

    def to_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "pages": self.pages,
            "safe": self.safe,
            "problems": self.problems,
            "spans_multiple_pages": self.spans_multiple_pages,
            "notices_on_same_page": self.notices_on_same_page,
            "table_ratio": round(self.table_ratio, 3),
            "truncated": self.truncated,
            "duplicated_pages": self.duplicated_pages,
            "body_chars": self.body_chars,
            "alerts": [a.to_dict() for a in self.alerts],
        }


def check_integrity(
    unit_id: str,
    pages: list[int],
    body: str,
    *,
    table_chars: int = 0,
    notices_per_page: dict[int, int] | None = None,
    duplicated_pages: set[int] | None = None,
    ends_with_signature: bool = True,
) -> IntegrityReport:
    """Contrôle l'intégrité d'une annonce avant extraction.

    ``notices_per_page`` et ``duplicated_pages`` viennent de l'analyse du
    document entier (``core.layout`` et ``qa.duplicates``) : une page ne peut
    pas savoir seule qu'elle est dupliquée.
    """
    notices_per_page = notices_per_page or {}
    duplicated_pages = duplicated_pages or set()
    body_chars = len(body.strip())

    report = IntegrityReport(
        unit_id=unit_id,
        pages=sorted(set(pages)),
        spans_multiple_pages=len(set(pages)) > 1,
        notices_on_same_page=max((notices_per_page.get(p, 1) for p in pages), default=1),
        table_ratio=(table_chars / body_chars) if body_chars else 0.0,
        truncated=not ends_with_signature,
        duplicated_pages=sorted(p for p in pages if p in duplicated_pages),
        body_chars=body_chars,
    )
    report.alerts = _integrity_alerts(report)
    return report


def _integrity_alerts(report: IntegrityReport) -> list[Alert]:
    alerts: list[Alert] = []
    common = {"page_refs": report.pages, "unit_id": report.unit_id}

    if report.spans_multiple_pages:
        alerts.append(Alert(
            code=Code.ANNONCE_MULTIPAGE, severity=Severity.INFO,
            message=f"Annonce étalée sur les pages {report.pages}",
            hint="Vérifier que le corps extrait couvre bien toutes ces pages.", **common,
        ))
    if report.notices_on_same_page > 1:
        alerts.append(Alert(
            code=Code.SECTION_INCOHERENTE, severity=Severity.ERROR,
            message=f"{report.notices_on_same_page} annonces partagent la page "
                    f"{report.pages[0]} : risque de mélange à l'extraction",
            hint="Le texte de l'annonce voisine peut être capté : vérifier la "
                 "délimitation avant d'exploiter les valeurs.", **common,
        ))
    if report.table_ratio >= TABLE_CHARS_RATIO_ALERT:
        alerts.append(Alert(
            code=Code.CHAMP_SUSPECT, severity=Severity.WARNING,
            message=f"{report.table_ratio:.0%} du corps de l'annonce est en tableaux",
            hint="Les cellules de tableau ressemblent à des valeurs et polluent "
                 "l'extraction : contrôler les champs courts en priorité.", **common,
        ))
    if report.truncated:
        alerts.append(Alert(
            code=Code.ANNONCE_TRONQUEE, severity=Severity.ERROR,
            message=f"Corps de l'annonce probablement tronqué (pages {report.pages})",
            hint="Ni signature ni formule de clôture : la suite manque, donc "
                 "les dernières clauses (dates, garantie) aussi.", **common,
        ))
    if report.duplicated_pages:
        alerts.append(Alert(
            code=Code.PAGE_DUPLIQUEE, severity=Severity.CRITICAL,
            message=f"Annonce assise sur une page dupliquée {report.duplicated_pages}",
            hint="Écarter le doublon avant extraction évite de payer deux fois "
                 "le même appel au modèle.", **common,
        ))
    if report.body_chars < MIN_BODY_CHARS:
        alerts.append(Alert(
            code=Code.PAGE_VIDE, severity=Severity.ERROR,
            message=f"Corps de l'annonce très court ({report.body_chars} caractères)",
            hint="Découpage trop serré, ou page dont le texte n'a pas été reconnu.", **common,
        ))
    return alerts


def notices_per_page(page_numbers: list[int]) -> dict[int, int]:
    """Combien d'annonces commencent sur chaque page."""
    counts: dict[int, int] = {}
    for page in page_numbers:
        counts[page] = counts.get(page, 0) + 1
    return counts


def table_chars_in(doc: Document, pages: list[int]) -> int:
    """Volume de texte provenant de tableaux sur les pages d'une annonce."""
    total = 0
    for page in doc.pages:
        if page.page_number in pages:
            total += sum(len(b.text) for b in page.blocks if b.type == "table")
    return total
