"""Estimation de la fiabilité d'une annonce extraite.

La question posée est : « peut-on publier cette annonce sans qu'un humain la
relise ? ». Un simple comptage de champs remplis n'y répond pas — il met une
date limite de dépôt manquante au même niveau qu'un horaire de retrait absent.

Trois dimensions sont donc notées séparément, puis combinées :

* **complétude** — part du poids des champs attendus effectivement renseignés,
  pondérée par la priorité (cf. ``core.priority``) ;
* **exactitude** — part des champs renseignés qui passent les contrôles de
  forme, de plausibilité et d'ancrage (cf. ``qa.rules``) ;
* **structure** — confiance dans le classement du titre et dans le découpage en
  pages (cf. ``core.titles``, ``qa.multipage``).

Les trois sont conservées dans le résultat : un score global unique ne dit pas
à l'opérateur *quoi* corriger, alors que « complétude 0.95 / exactitude 0.40 »
désigne immédiatement un problème de qualité de lecture, pas de couverture.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.priority import FieldProfile, Priority
from ..core.titles import TitleKind
from .alerts import Alert, Code, Severity
from .rules import FieldCheck, Status, check_record, is_placeholder, parse_amount, parse_date

# Pondération du score global. L'exactitude pèse le plus : une annonce
# incomplète est inexploitable mais honnête, une annonce fausse pollue la base.
WEIGHT_COMPLETENESS = 0.35
WEIGHT_ACCURACY = 0.45
WEIGHT_STRUCTURE = 0.20

# Pénalité appliquée à l'exactitude selon la nature du défaut.
_ACCURACY_PENALTY = {
    Status.OK: 0.0,
    Status.SUSPECT: 0.4,
    Status.INVALID: 0.8,
    Status.UNGROUNDED: 1.0,  # une valeur inventée est le pire des cas
    Status.MISSING: 0.0,     # l'absence est comptée par la complétude, pas ici
}


class Verdict(str, Enum):
    """Décision de tri proposée à l'opérateur."""

    VALIDE = "VALIDE"              # publiable en l'état
    A_VERIFIER = "A_VERIFIER"      # un doute ciblé, relecture rapide
    SUSPECT = "SUSPECT"            # plusieurs défauts, relecture complète
    REJETE = "REJETE"              # inexploitable, à réextraire

    @property
    def label(self) -> str:
        return {
            "VALIDE": "Valide",
            "A_VERIFIER": "À vérifier",
            "SUSPECT": "Suspect",
            "REJETE": "Rejeté",
        }[self.value]


# Bornes de confiance → verdict. Un champ critique défaillant force au minimum
# A_VERIFIER quel que soit le score, car la moyenne peut masquer un trou.
THRESHOLD_VALIDE = 0.85
THRESHOLD_A_VERIFIER = 0.65
THRESHOLD_SUSPECT = 0.40


@dataclass
class Score:
    """Notes détaillées d'une annonce."""

    completeness: float
    accuracy: float
    structure: float
    confidence: float
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)
    missing_critical: list[str] = field(default_factory=list)
    problems: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "completeness": round(self.completeness, 3),
            "accuracy": round(self.accuracy, 3),
            "structure": round(self.structure, 3),
            "confidence": round(self.confidence, 3),
            "verdict": self.verdict.value,
            "verdict_label": self.verdict.label,
            "reasons": self.reasons,
            "missing_critical": self.missing_critical,
            "problems": self.problems,
        }


def score_completeness(checks: dict[str, FieldCheck], profile: FieldProfile) -> tuple[float, list[str]]:
    """Part du poids des champs attendus qui est renseignée."""
    total = profile.total_weight
    if not total:
        return 1.0, []
    earned = 0
    missing_critical: list[str] = []
    for name, check in checks.items():
        weight = int(profile.priority(name))
        if check.status is Status.MISSING:
            if profile.priority(name) is Priority.CRITICAL:
                missing_critical.append(name)
            continue
        earned += weight
    return earned / total, missing_critical


def score_accuracy(checks: dict[str, FieldCheck], profile: FieldProfile) -> tuple[float, dict[str, str]]:
    """Qualité des champs effectivement renseignés, pondérée par priorité."""
    weighted_total = 0
    penalty = 0.0
    problems: dict[str, str] = {}
    for name, check in checks.items():
        if check.status is Status.MISSING:
            continue
        weight = int(profile.priority(name))
        weighted_total += weight
        cost = _ACCURACY_PENALTY.get(check.status, 0.0)
        if cost:
            penalty += weight * cost
            problems[name] = f"{check.status.value}: {check.reason}"
    if not weighted_total:
        # Aucun champ renseigné : l'exactitude n'est pas évaluable. On la met à
        # 0 pour ne pas décerner une bonne note à une extraction vide.
        return 0.0, problems
    return max(0.0, 1 - penalty / weighted_total), problems


def score_structure(
    title_confidence: float,
    span_confidence: float,
    kind: TitleKind,
    ocr_confidence: float = 1.0,
) -> tuple[float, list[str]]:
    """Confiance dans le fait d'avoir isolé et classé la bonne annonce."""
    reasons = []
    value = 0.4 * title_confidence + 0.4 * span_confidence + 0.2 * ocr_confidence
    if kind is TitleKind.UNKNOWN:
        value *= 0.6
        reasons.append("la nature de l'annonce n'a pas été reconnue depuis son titre")
    if span_confidence < 0.6:
        reasons.append("le rattachement des pages à cette annonce est incertain")
    if ocr_confidence < 0.7:
        reasons.append("la qualité de reconnaissance de texte est faible sur ces pages")
    return min(1.0, value), reasons


def decide_verdict(confidence: float, missing_critical: list[str], problems: dict[str, str]) -> Verdict:
    """Traduit les notes en décision, avec garde-fous sur les champs critiques."""
    if missing_critical:
        # Un champ critique absent interdit « valide », même avec un bon score :
        # publier une annonce sans date limite de dépôt n'a aucun sens.
        return Verdict.REJETE if len(missing_critical) > 1 or confidence < THRESHOLD_SUSPECT else Verdict.SUSPECT
    if confidence >= THRESHOLD_VALIDE:
        return Verdict.VALIDE if not problems else Verdict.A_VERIFIER
    if confidence >= THRESHOLD_A_VERIFIER:
        return Verdict.A_VERIFIER
    if confidence >= THRESHOLD_SUSPECT:
        return Verdict.SUSPECT
    return Verdict.REJETE


def score_record(
    record: dict,
    profile: FieldProfile,
    source_text: str = "",
    title_confidence: float = 0.7,
    span_confidence: float = 0.9,
    ocr_confidence: float = 1.0,
) -> tuple[Score, dict[str, FieldCheck]]:
    """Note une annonce extraite et retourne aussi le détail par champ."""
    checks = check_record(record, list(profile.priorities), source_text)
    checks.update(_coherence_checks(record))

    completeness, missing_critical = score_completeness(checks, profile)
    accuracy, problems = score_accuracy(checks, profile)
    structure, struct_reasons = score_structure(
        title_confidence, span_confidence, profile.kind, ocr_confidence
    )

    confidence = (
        WEIGHT_COMPLETENESS * completeness
        + WEIGHT_ACCURACY * accuracy
        + WEIGHT_STRUCTURE * structure
    )
    verdict = decide_verdict(confidence, missing_critical, problems)

    reasons = list(struct_reasons)
    if missing_critical:
        reasons.append("champ(s) critique(s) absent(s) : " + ", ".join(missing_critical))
    if problems:
        reasons.append(f"{len(problems)} champ(s) en défaut de qualité")

    return (
        Score(
            completeness=completeness,
            accuracy=accuracy,
            structure=structure,
            confidence=confidence,
            verdict=verdict,
            reasons=reasons,
            missing_critical=missing_critical,
            problems=problems,
        ),
        checks,
    )


def _coherence_checks(record: dict) -> dict[str, FieldCheck]:
    """Contrôles croisés entre champs, qu'un contrôle isolé ne peut pas voir."""
    out: dict[str, FieldCheck] = {}

    lots = record.get("lots")
    en_lots = record.get("est_en_lots")
    if en_lots is True and is_placeholder(lots):
        out["lots"] = FieldCheck(
            "lots", Status.INVALID,
            "marché déclaré alloti mais aucun lot n'est détaillé",
            hint="Le tableau des lots est probablement resté dans la page sans être extrait.",
        )
    elif en_lots is False and isinstance(lots, list) and len(lots) > 1:
        out["est_en_lots"] = FieldCheck(
            "est_en_lots", Status.INVALID,
            f"marché déclaré non alloti alors que {len(lots)} lots sont listés",
        )

    mini, maxi = parse_amount(str(record.get("montant_attribue_min") or "")), parse_amount(
        str(record.get("montant_attribue_max") or "")
    )
    if mini and maxi and mini > maxi:
        out["montant_attribue_max"] = FieldCheck(
            "montant_attribue_max", Status.INVALID,
            f"montant minimum ({mini}) supérieur au maximum ({maxi})",
            hint="Les deux montants ont probablement été inversés à l'extraction.",
        )

    depot, ouverture = parse_date(str(record.get("date_limite_depot") or "")), parse_date(
        str(record.get("date_ouverture_plis") or "")
    )
    if depot and ouverture and ouverture < depot:
        out["date_ouverture_plis"] = FieldCheck(
            "date_ouverture_plis", Status.INVALID,
            "ouverture des plis antérieure à la date limite de dépôt",
            hint="Réglementairement impossible : l'une des deux dates est mal lue.",
        )
    return out


def field_alerts(
    checks: dict[str, FieldCheck],
    profile: FieldProfile,
    unit_id: str,
    page_refs: list[int],
) -> list[Alert]:
    """Transforme les verdicts de champ en alertes, graduées par priorité."""
    alerts: list[Alert] = []
    for name, check in checks.items():
        if check.ok:
            continue
        priority = profile.priority(name)

        if check.status is Status.MISSING:
            if priority is Priority.CRITICAL:
                code, severity = Code.CHAMP_CRITIQUE_MANQUANT, Severity.ERROR
            elif priority is Priority.IMPORTANT:
                code, severity = Code.CHAMP_IMPORTANT_MANQUANT, Severity.WARNING
            else:
                continue  # un champ optionnel absent n'est pas un événement
            message = f"Champ {priority.label} « {name} » absent de l'extraction"
        elif check.status is Status.UNGROUNDED:
            code, severity = Code.CHAMP_NON_ANCRE, Severity.ERROR
            message = f"Valeur de « {name} » introuvable dans la page : {check.reason}"
        elif check.status is Status.INVALID:
            code = Code.CHAMP_INVALIDE
            severity = Severity.ERROR if priority >= Priority.IMPORTANT else Severity.WARNING
            message = f"Champ « {name} » invalide : {check.reason}"
        else:
            code = Code.CHAMP_SUSPECT
            severity = Severity.WARNING if priority >= Priority.IMPORTANT else Severity.INFO
            message = f"Champ « {name} » douteux : {check.reason}"

        alerts.append(
            Alert(
                code=code,
                severity=severity,
                message=message,
                page_refs=page_refs,
                unit_id=unit_id,
                field_name=name,
                hint=check.hint,
                evidence=check.evidence,
            )
        )
    return alerts
