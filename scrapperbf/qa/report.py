"""Orchestration du contrôle qualité d'un bulletin.

Assemble les briques en un seul rapport exploitable :

    Markdown (ou PDF via ``ocr.light_ocr``)
      → pages numérotées         (core.pages)
      → annonces + nature        (core.segment, core.titles)
      → champs attendus          (core.priority)
      → contrôles et notes       (qa.rules, qa.scoring)
      → doubles et continuité    (qa.duplicates, qa.multipage)
      → alertes triées           (qa.alerts)
      → rapport JSON + dashboard

Le rapport est la seule interface entre le pipeline et l'humain : le dashboard
le lit, la CLI l'affiche, et les retours d'opérateurs y sont rapportés par
identifiant d'annonce.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.pages import Document, parse_file, parse_pages
from ..core.priority import profile_for
from ..core.segment import Notice, segment
from ..core.titles import TitleKind
from .alerts import Alert, Code, Severity, sort_alerts, summarize
from .duplicates import check_pagination, duplicate_alerts, find_duplicate_pages, find_duplicate_units
from .multipage import analyze_document as analyze_continuity
from .rules import FieldCheck
from .scoring import Score, Verdict, field_alerts, score_record, score_structure

if TYPE_CHECKING:  # pragma: no cover - uniquement pour le typage
    from ..feedback.store import FeedbackStore

# Une alerte « calmée » par les retours humains n'est pas supprimée : sa
# gravité est abaissée d'un cran. Supprimer reviendrait à décider que
# l'opérateur avait toujours raison, y compris sur les cas qu'il n'a pas vus.
MUTED_SEVERITY_FLOOR = Severity.INFO


@dataclass
class NoticeAudit:
    """Résultat du contrôle d'une annonce."""

    notice: Notice
    score: Score
    checks: dict[str, FieldCheck]
    alerts: list[Alert]
    extracted: dict | None
    span_pages: list[int] = field(default_factory=list)
    span_confidence: float = 0.9

    @property
    def priority_fields(self) -> dict[str, list[str]]:
        profile = profile_for(self.notice.kind)
        from ..core.priority import Priority

        return {
            "critique": profile.fields_at(Priority.CRITICAL),
            "important": profile.fields_at(Priority.IMPORTANT),
        }

    def to_dict(self) -> dict:
        return {
            **self.notice.to_dict(),
            "score": self.score.to_dict(),
            "checks": {
                name: {
                    "status": c.status.value,
                    "reason": c.reason,
                    "hint": c.hint,
                    "normalized": c.normalized if isinstance(c.normalized, (str, int, float, bool, type(None))) else str(c.normalized),
                }
                for name, c in self.checks.items()
            },
            "alerts": [a.to_dict() for a in sort_alerts(self.alerts)],
            "extracted": self.extracted,
            "priority_fields": self.priority_fields,
            "span_pages": self.span_pages,
            "span_confidence": round(self.span_confidence, 3),
        }


@dataclass
class AuditReport:
    """Rapport complet d'un bulletin."""

    document: str
    source: str
    generated_at: str
    page_count: int
    notices: list[NoticeAudit] = field(default_factory=list)
    document_alerts: list[Alert] = field(default_factory=list)
    page_signals: list[dict] = field(default_factory=list)
    spans: list[dict] = field(default_factory=list)
    duplicate_pages: list[dict] = field(default_factory=list)
    duplicate_notices: list[dict] = field(default_factory=list)
    pagination: list[dict] = field(default_factory=list)
    ocr: dict | None = None
    muted_codes: dict[str, float] = field(default_factory=dict)

    @property
    def all_alerts(self) -> list[Alert]:
        return sort_alerts(self.document_alerts + [a for n in self.notices for a in n.alerts])

    @property
    def stats(self) -> dict:
        verdicts = {v.value: 0 for v in Verdict}
        kinds: dict[str, int] = {}
        for audit in self.notices:
            verdicts[audit.score.verdict.value] += 1
            kinds[audit.notice.kind.value] = kinds.get(audit.notice.kind.value, 0) + 1
        confidences = [a.score.confidence for a in self.notices]
        alerts = self.all_alerts
        return {
            "annonces": len(self.notices),
            "pages": self.page_count,
            "verdicts": verdicts,
            "natures": kinds,
            "confiance_moyenne": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
            "a_relire": sum(
                1 for a in self.notices
                if a.score.verdict in (Verdict.A_VERIFIER, Verdict.SUSPECT, Verdict.REJETE)
            ),
            "alertes": summarize(alerts),
            "codes_alertes": _count_codes(alerts),
        }

    def to_dict(self) -> dict:
        return {
            "document": self.document,
            "source": self.source,
            "generated_at": self.generated_at,
            "stats": self.stats,
            "notices": [n.to_dict() for n in self.notices],
            "document_alerts": [a.to_dict() for a in sort_alerts(self.document_alerts)],
            "page_signals": self.page_signals,
            "spans": self.spans,
            "duplicate_pages": self.duplicate_pages,
            "duplicate_notices": self.duplicate_notices,
            "pagination": self.pagination,
            "ocr": self.ocr,
            "muted_codes": self.muted_codes,
        }

    def to_text(self, max_alerts: int = 40) -> str:
        """Résumé lisible en console, trié par gravité."""
        stats = self.stats
        lines = [
            f"Rapport QA — {self.document} ({self.source})",
            f"  {stats['annonces']} annonces sur {stats['pages']} pages "
            f"| confiance moyenne {stats['confiance_moyenne']:.0%} "
            f"| {stats['a_relire']} à relire",
            "  verdicts : " + ", ".join(f"{k}={v}" for k, v in stats["verdicts"].items() if v),
            "  natures  : " + (", ".join(f"{k}={v}" for k, v in stats["natures"].items()) or "aucune"),
            "  alertes  : " + ", ".join(f"{k}={v}" for k, v in stats["alertes"].items() if v),
            "",
        ]
        for alert in self.all_alerts[:max_alerts]:
            prefix = f"  {alert.unit_id or 'document'}"
            lines.append(f"{prefix} {alert.human()}")
        remaining = len(self.all_alerts) - max_alerts
        if remaining > 0:
            lines.append(f"  … et {remaining} autre(s) alerte(s) (rapport JSON complet)")
        return "\n".join(lines)


def _count_codes(alerts: list[Alert]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for a in alerts:
        counts[a.code] = counts.get(a.code, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _apply_muting(alerts: list[Alert], muted: dict[str, float]) -> list[Alert]:
    """Abaisse la gravité des codes jugés majoritairement faux positifs."""
    if not muted:
        return alerts
    for alert in alerts:
        if alert.code in muted:
            lowered = max(MUTED_SEVERITY_FLOOR, Severity(int(alert.severity) - 1))
            alert.severity = lowered
            rate = muted[alert.code]
            alert.hint = (
                f"{alert.hint} (gravité abaissée : {rate:.0%} des retours humains sur "
                f"« {alert.code} » étaient des faux positifs)"
            ).strip()
    return alerts


def _span_for(notice: Notice, spans: list[dict]) -> tuple[list[int], float]:
    """Retrouve le groupe de pages auquel l'annonce appartient."""
    for span in spans:
        if notice.page_debut in span["pages"]:
            return span["pages"], span["confidence"]
    return notice.pages, 0.9


def audit_document(
    doc: Document,
    document_name: str,
    extractions: dict[str, dict] | None = None,
    ocr_confidence: dict[int, float] | None = None,
    ocr_summary: dict | None = None,
    store: "FeedbackStore | None" = None,
) -> AuditReport:
    """Contrôle un document déjà parsé.

    ``extractions`` associe un identifiant d'annonce au dict renvoyé par la
    couche 3. Sans extractions, le rapport reste utile : il contrôle la
    structure (titres, doubles, continuité), ce qui se fait sans aucun appel
    LLM et permet d'écarter un PDF défectueux *avant* de dépenser des jetons.
    """
    notices = segment(doc)
    signals, spans, continuity_alerts = analyze_continuity(doc)
    page_groups = find_duplicate_pages(doc)
    pagination = check_pagination(doc)

    span_dicts = [s.to_dict() for s in spans]
    ocr_confidence = ocr_confidence or {}

    audits: list[NoticeAudit] = []
    for notice in notices:
        profile = profile_for(notice.kind)
        span_pages, span_conf = _span_for(notice, span_dicts)
        page_conf = [ocr_confidence.get(p, 1.0) for p in notice.pages] or [1.0]
        mean_ocr = sum(page_conf) / len(page_conf)

        record = (extractions or {}).get(notice.id)
        alerts: list[Alert] = []

        if record is None:
            # Pas d'extraction : on note ce qu'on peut (la structure) et on le
            # dit, au lieu d'attribuer un zéro qui ressemblerait à une erreur
            # du modèle.
            structure, struct_reasons = score_structure(
                notice.kind_confidence, span_conf, notice.kind, mean_ocr
            )
            score = Score(
                completeness=0.0, accuracy=0.0, structure=structure,
                confidence=round(structure * 0.2, 3),
                verdict=Verdict.REJETE if extractions is not None else Verdict.A_VERIFIER,
                reasons=(["aucune extraction disponible pour cette annonce"] if extractions is not None
                         else ["audit structurel seul : extraction non demandée"]) + struct_reasons,
            )
            checks: dict[str, FieldCheck] = {}
            if extractions is not None:
                alerts.append(
                    Alert(
                        code=Code.EXTRACTION_ECHOUEE,
                        severity=Severity.ERROR,
                        message="Aucune donnée extraite pour cette annonce",
                        page_refs=notice.pages,
                        unit_id=notice.id,
                        hint="Relancer l'extraction sur cette unité (voir reprocess_failures).",
                    )
                )
        else:
            score, checks = score_record(
                record, profile, source_text=notice.flat_text,
                title_confidence=notice.kind_confidence,
                span_confidence=span_conf,
                ocr_confidence=mean_ocr,
            )
            alerts.extend(field_alerts(checks, profile, notice.id, notice.pages))

        alerts.extend(_classification_alerts(notice))
        if mean_ocr < 0.7:
            alerts.append(
                Alert(
                    code=Code.OCR_DEGRADE,
                    severity=Severity.WARNING,
                    message=f"Qualité de reconnaissance faible sur ces pages ({mean_ocr:.0%})",
                    page_refs=notice.pages,
                    unit_id=notice.id,
                    hint="Les défauts de champs ci-dessous viennent probablement de la numérisation, "
                         "pas du modèle d'extraction.",
                )
            )

        audits.append(
            NoticeAudit(
                notice=notice, score=score, checks=checks, alerts=alerts,
                extracted=record, span_pages=span_pages, span_confidence=span_conf,
            )
        )

    unit_groups = find_duplicate_units([a.notice.to_dict() for a in audits])
    document_alerts = continuity_alerts + duplicate_alerts(page_groups, unit_groups, pagination)

    muted = store.muted_alert_codes() if store else {}
    _apply_muting(document_alerts, muted)
    for audit in audits:
        _apply_muting(audit.alerts, muted)

    return AuditReport(
        document=document_name,
        source=doc.source,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        page_count=len(doc.pages),
        notices=audits,
        document_alerts=document_alerts,
        page_signals=[s.to_dict() for s in signals],
        spans=span_dicts,
        duplicate_pages=[g.to_dict() for g in page_groups],
        duplicate_notices=[g.to_dict() for g in unit_groups],
        pagination=[{"kind": a.kind, "pages": a.pages, "detail": a.detail} for a in pagination],
        ocr=ocr_summary,
        muted_codes=muted,
    )


def _classification_alerts(notice: Notice) -> list[Alert]:
    """Alertes sur le tri lui-même, pas sur les champs."""
    alerts = []
    if not notice.title:
        alerts.append(
            Alert(
                code=Code.TITRE_ABSENT,
                severity=Severity.ERROR,
                message="Annonce sans titre identifié : sa nature est inconnue",
                page_refs=notice.pages,
                unit_id=notice.id,
                hint="Sans nature, les champs attendus ne peuvent pas être déterminés : "
                     "retrouver le titre dans le PDF et le corriger.",
                evidence=[notice.split_reason],
            )
        )
    elif notice.kind is TitleKind.UNKNOWN:
        alerts.append(
            Alert(
                code=Code.TITRE_NON_CLASSE,
                severity=Severity.WARNING,
                message=f"Titre non classé : « {notice.title[:90]} »",
                page_refs=notice.pages,
                unit_id=notice.id,
                hint="Nature d'annonce inconnue du catalogue : vérifier, et si le libellé est "
                     "récurrent, l'ajouter aux motifs de core/titles.py.",
            )
        )
    elif notice.kind_confidence < 0.6:
        alerts.append(
            Alert(
                code=Code.CLASSEMENT_INCERTAIN,
                severity=Severity.WARNING,
                message=f"Classement peu sûr ({notice.kind.value}, {notice.kind_confidence:.0%}) "
                        f"pour « {notice.title[:70]} »",
                page_refs=notice.pages,
                unit_id=notice.id,
                hint="Confirmer la nature de l'annonce : les champs contrôlés en dépendent.",
                evidence=notice.title_evidence,
            )
        )
    return alerts


def audit_markdown(
    md_path: str | Path,
    document_name: str | None = None,
    extractions: dict[str, dict] | None = None,
    store: "FeedbackStore | None" = None,
) -> AuditReport:
    doc = parse_file(md_path)
    return audit_document(
        doc, document_name or Path(md_path).stem, extractions=extractions, store=store
    )


def audit_text(md_text: str, document_name: str = "document", **kwargs) -> AuditReport:
    return audit_document(parse_pages(md_text, source="<texte>"), document_name, **kwargs)
