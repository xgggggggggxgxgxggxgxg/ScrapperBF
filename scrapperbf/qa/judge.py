"""Le juge : estimer en pourcentage si une extraction est fiable.

Un modèle qui annonce « confiance 0.95 » ne prouve rien : il est aussi sûr de
lui quand il se trompe. Le juge ne le croit donc pas sur parole, et note chaque
champ sur trois éléments **vérifiables** :

* **l'ancrage** — la citation que le modèle fournit existe-t-elle réellement
  dans le texte de l'annonce ? C'est le contrôle décisif : une citation
  introuvable signale une valeur fabriquée ;
* **les critères du référentiel** — ``extraction_rules.yaml`` donne par champ
  des règles contrôlables mécaniquement (la date de dépôt précède l'ouverture
  des plis, le montant en lettres égale le montant en chiffres, un téléphone a
  huit chiffres…) ;
* **la forme** — les contrôles de ``qa.rules`` (type, plausibilité).

La confiance déclarée par le modèle n'entre que pour une petite part, et ne
peut jamais à elle seule faire passer un champ pour fiable.

Le score global est la moyenne de ces notes pondérée par la priorité des
champs, exprimée en pourcentage. En dessous du seuil, l'annonce part en
relecture humaine : c'est le branchement décrit dans ``extract.cascade``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..core.priority import FieldProfile, Priority
from ..core.text import normalize
from ..extract.numerals import cross_check_amount
from ..extract.prompt import load_rules
from .alerts import Alert, Code, Severity
from .rules import Status, check_field, is_placeholder, parse_amount, parse_date

# Pondération des composantes d'une note de champ. L'ancrage domine : c'est le
# seul contrôle qui distingue une lecture d'une invention.
POIDS_ANCRAGE = 0.45
POIDS_CRITERES = 0.30
POIDS_FORME = 0.15
POIDS_CONFIANCE_MODELE = 0.10

# Un champ noté sous ce seuil est tenu pour non fiable, quelle que soit la
# moyenne générale.
SEUIL_CHAMP_FIABLE = 0.60
# Seuil de fiabilité global, en dessous duquel une relecture humaine est due.
SEUIL_FIABILITE_GLOBALE = 0.75
# Part minimale de la citation qui doit se retrouver dans le texte.
ANCRAGE_MIN_RECOUVREMENT = 0.75


@dataclass
class FieldVerdict:
    """Note détaillée d'un champ."""

    field: str
    reliability: float
    anchored: bool | None
    criteria_passed: list[str] = field(default_factory=list)
    criteria_failed: list[str] = field(default_factory=list)
    form_status: str = "ok"
    model_confidence: float | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def reliable(self) -> bool:
        return self.reliability >= SEUIL_CHAMP_FIABLE

    @property
    def percent(self) -> int:
        return round(self.reliability * 100)

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "reliability": round(self.reliability, 3),
            "percent": self.percent,
            "reliable": self.reliable,
            "anchored": self.anchored,
            "criteria_passed": self.criteria_passed,
            "criteria_failed": self.criteria_failed,
            "form_status": self.form_status,
            "model_confidence": self.model_confidence,
            "reasons": self.reasons,
        }


@dataclass
class Judgement:
    """Verdict d'ensemble sur une extraction."""

    unit_id: str
    score: float
    fields: dict[str, FieldVerdict] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)

    @property
    def percent(self) -> int:
        return round(self.score * 100)

    @property
    def reliable(self) -> bool:
        return self.score >= SEUIL_FIABILITE_GLOBALE

    @property
    def unreliable_fields(self) -> list[str]:
        return sorted(n for n, v in self.fields.items() if not v.reliable)

    def to_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "score": round(self.score, 3),
            "percent": self.percent,
            "reliable": self.reliable,
            "unreliable_fields": self.unreliable_fields,
            "fields": {n: v.to_dict() for n, v in self.fields.items()},
            "alerts": [a.to_dict() for a in self.alerts],
        }


def _tokens(texte: str) -> list[str]:
    return [t for t in re.split(r"[^0-9a-z]+", normalize(texte)) if t]


def check_anchoring(citation: str, source: str) -> tuple[bool | None, float, str]:
    """La citation du modèle se retrouve-t-elle dans le texte source ?

    On compare par recouvrement de tokens et non par égalité : les modèles
    normalisent les espaces, les apostrophes et la casse en recopiant, et exiger
    le littéral exact rejetterait des citations honnêtes. Retourne
    ``(ancrée, recouvrement, motif)`` ; ``None`` quand il n'y a pas de citation
    à vérifier.
    """
    if not citation or not citation.strip():
        return None, 0.0, "aucune citation fournie"
    if not source:
        return None, 0.0, "aucun texte source pour vérifier"

    attendus = _tokens(citation)
    if not attendus:
        return None, 0.0, "citation sans contenu exploitable"
    presents = set(_tokens(source))
    retrouves = sum(1 for t in attendus if t in presents)
    recouvrement = retrouves / len(attendus)
    if recouvrement >= ANCRAGE_MIN_RECOUVREMENT:
        return True, recouvrement, f"citation retrouvée à {recouvrement:.0%}"
    return False, recouvrement, (
        f"citation retrouvée à {recouvrement:.0%} seulement : "
        "le modèle n'a pas recopié le texte de la page"
    )


def _criterion_literal(valeur: object, source: str) -> bool | None:
    """La valeur elle-même figure-t-elle dans le texte ?"""
    if is_placeholder(valeur) or not source:
        return None
    if isinstance(valeur, (dict, list)):
        return None
    attendus = _tokens(str(valeur))
    if not attendus:
        return None
    presents = set(_tokens(source))
    return sum(1 for t in attendus if t in presents) / len(attendus) >= 0.7


def _criterion_digits(valeur: object, source: str) -> bool | None:
    """Les chiffres de la valeur figurent-ils dans le texte ?"""
    if is_placeholder(valeur) or not source:
        return None
    brut = valeur if isinstance(valeur, str) else str(valeur)
    nombres = [re.sub(r"\D", "", n) for n in re.findall(r"\d[\d\s.  ]*\d|\d", brut)]
    nombres = [n for n in nombres if len(n) >= 3]
    if not nombres:
        return None
    chiffres_source = re.sub(r"\D", "", source)
    return all(n in chiffres_source for n in nombres)


def evaluate_criteria(
    name: str,
    valeur: object,
    source: str,
    record: dict,
    *,
    bulletin_date: date | None = None,
    rules: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Applique les critères contrôlables du référentiel à un champ.

    Seuls les critères effectivement vérifiables par programme sont évalués :
    les autres restent dans le YAML pour instruire le modèle, et le juge ne
    prétend pas les avoir contrôlés.
    """
    reussis: list[str] = []
    echoues: list[str] = []
    rules = rules if rules is not None else load_rules()
    spec = (rules.get("champs") or {}).get(name) or {}
    criteres = spec.get("verification") or []

    for critere in criteres:
        texte = normalize(critere)
        verdict: bool | None = None

        if "apparait litteralement" in texte or "apparaissent litteralement" in texte:
            verdict = _criterion_literal(valeur, source)
        elif "chiffres apparaissent" in texte:
            verdict = _criterion_digits(valeur, source)
        elif "contient un nombre et une unite de temps" in texte:
            verdict = bool(re.search(r"\d+\s*\)?\s*(jours?|mois|semaines?|ans?|annees?)",
                                     normalize(str(valeur))))
        elif "au moins un chiffre et une barre oblique" in texte:
            v = normalize(str(valeur))
            verdict = bool(re.search(r"\d", v)) and bool(re.search(r"[-/]", v))
        elif "huit chiffres" in texte:
            verdict = _criterion_phones(valeur)
        elif "montant en lettres" in texte:
            verdict = _criterion_amount_words(valeur, source)
        elif "posterieure ou egale a la date de parution" in texte and bulletin_date:
            verdict = _criterion_after(valeur, bulletin_date)
        elif "anterieure ou egale a la date d'ouverture" in texte:
            verdict = _criterion_order(valeur, record.get("date_ouverture_plis"))
        elif "posterieure ou egale a la date limite de depot" in texte:
            verdict = _criterion_order(record.get("date_limite_depot"), valeur)
        elif "inferieur au montant previsionnel" in texte:
            verdict = _criterion_below(valeur, record.get("montant_previsionnel_global"))
        elif "numeros de lot sont uniques et consecutifs" in texte:
            verdict = _criterion_lots(valeur)

        if verdict is True:
            reussis.append(critere)
        elif verdict is False:
            echoues.append(critere)
    return reussis, echoues


def _criterion_phones(valeur: object) -> bool | None:
    numeros = []
    if isinstance(valeur, dict):
        numeros = valeur.get("telephones") or []
    elif isinstance(valeur, list):
        numeros = valeur
    if not numeros:
        return None
    for numero in numeros:
        chiffres = re.sub(r"\D", "", str(numero))
        chiffres = chiffres[-8:] if len(chiffres) > 8 else chiffres
        if len(chiffres) != 8:
            return False
    return True


def _criterion_amount_words(valeur: object, source: str) -> bool | None:
    """Le montant en lettres et le montant en chiffres concordent-ils ?"""
    segment = ""
    if isinstance(valeur, dict):
        segment = " ".join(str(v) for v in valeur.values())
    else:
        segment = str(valeur)
    lettres, chiffres, concordent = cross_check_amount(segment)
    if concordent is None and source:
        # La version en lettres est parfois restée dans le texte sans être
        # reprise dans la valeur : on la cherche là où elle se trouve.
        chiffre_cible = chiffres or parse_amount(segment)
        if chiffre_cible:
            for phrase in re.findall(r"[^.\n]{0,160}\(\s*[\d\s.]{3,}\s*\)", source):
                l, c, ok = cross_check_amount(phrase)
                if c == chiffre_cible and ok is not None:
                    return ok
    return concordent


def _criterion_after(valeur: object, reference: date) -> bool | None:
    d = parse_date(str(valeur))
    if d is None:
        return None
    return date(*d) >= reference


def _criterion_order(avant: object, apres: object) -> bool | None:
    a, b = parse_date(str(avant or "")), parse_date(str(apres or ""))
    if a is None or b is None:
        # Une ouverture « immédiate » n'est pas une date : il n'y a rien à
        # ordonner, et ce n'est pas un échec.
        return None
    return date(*a) <= date(*b)


def _criterion_below(petit: object, grand: object) -> bool | None:
    a = parse_amount(str(petit or ""))
    b = parse_amount(str(grand) if not isinstance(grand, dict)
                     else " ".join(str(v) for v in grand.values()))
    if a is None or b is None:
        return None
    return a <= b


def _criterion_lots(valeur: object) -> bool | None:
    if not isinstance(valeur, list) or not valeur:
        return None
    numeros = [l.get("numero") for l in valeur if isinstance(l, dict)]
    numeros = [n for n in numeros if isinstance(n, int)]
    if not numeros:
        return None
    return sorted(numeros) == list(range(1, len(numeros) + 1))


def judge_field(
    name: str,
    valeur: object,
    *,
    citation: str = "",
    model_confidence: float | None = None,
    source: str = "",
    record: dict | None = None,
    bulletin_date: date | None = None,
    rules: dict | None = None,
) -> FieldVerdict:
    """Note la fiabilité d'un champ, entre 0 et 1."""
    record = record or {}
    raisons: list[str] = []

    if is_placeholder(valeur):
        # Un champ vide n'est pas « peu fiable » : il est absent. C'est la
        # couverture qui en juge, pas le juge de fiabilité.
        return FieldVerdict(
            field=name, reliability=0.0, anchored=None,
            form_status=Status.MISSING.value, model_confidence=model_confidence,
            reasons=["champ absent : relève de la couverture, non de la fiabilité"],
        )

    ancree, recouvrement, motif_ancrage = check_anchoring(citation, source)
    raisons.append(motif_ancrage)
    if ancree is True:
        note_ancrage = min(1.0, recouvrement)
    elif ancree is False:
        note_ancrage = recouvrement * 0.5
    else:
        # Sans citation, on se rabat sur la présence de la valeur elle-même.
        litteral = _criterion_literal(valeur, source)
        note_ancrage = 0.75 if litteral else (0.25 if litteral is False else 0.5)
        raisons.append("ancrage estimé d'après la valeur, faute de citation")

    reussis, echoues = evaluate_criteria(
        name, valeur, source, record, bulletin_date=bulletin_date, rules=rules
    )
    total_criteres = len(reussis) + len(echoues)
    note_criteres = (len(reussis) / total_criteres) if total_criteres else 0.7
    if not total_criteres:
        raisons.append("aucun critère mécaniquement vérifiable pour ce champ")

    verdict_forme = check_field(name, valeur, source)
    note_forme = {
        Status.OK: 1.0, Status.SUSPECT: 0.5, Status.INVALID: 0.15,
        Status.UNGROUNDED: 0.0, Status.MISSING: 0.0,
    }[verdict_forme.status]
    if verdict_forme.status is not Status.OK:
        raisons.append(f"forme : {verdict_forme.reason}")

    note_modele = model_confidence if model_confidence is not None else 0.5

    fiabilite = (
        POIDS_ANCRAGE * note_ancrage
        + POIDS_CRITERES * note_criteres
        + POIDS_FORME * note_forme
        + POIDS_CONFIANCE_MODELE * note_modele
    )
    # Un critère du référentiel qui échoue est un fait, pas une nuance : il
    # plafonne la note, sans quoi une bonne moyenne l'effacerait.
    if echoues:
        fiabilite = min(fiabilite, 0.55)
        raisons.append(f"{len(echoues)} critère(s) du référentiel non satisfait(s)")
    if ancree is False:
        fiabilite = min(fiabilite, 0.45)

    return FieldVerdict(
        field=name, reliability=round(min(1.0, fiabilite), 3), anchored=ancree,
        criteria_passed=reussis, criteria_failed=echoues,
        form_status=verdict_forme.status.value, model_confidence=model_confidence,
        reasons=raisons,
    )


def judge(
    unit_id: str,
    envelopes: dict[str, dict],
    profile: FieldProfile,
    *,
    source: str = "",
    pages: list[int] | None = None,
    bulletin_date: date | None = None,
    rules: dict | None = None,
) -> Judgement:
    """Note une extraction entière.

    ``envelopes`` est la réponse du modèle au format du prompt :
    ``{champ: {"valeur": …, "confiance": 0.9, "citation": "…"}}``. Une valeur
    nue est acceptée aussi, pour pouvoir juger une extraction par motifs.
    """
    rules = rules if rules is not None else load_rules()
    record = {
        name: (env.get("valeur") if isinstance(env, dict) and "valeur" in env else env)
        for name, env in envelopes.items()
    }

    verdicts: dict[str, FieldVerdict] = {}
    for name, env in envelopes.items():
        if isinstance(env, dict) and "valeur" in env:
            valeur, citation = env.get("valeur"), str(env.get("citation") or "")
            confiance = env.get("confiance")
            confiance = float(confiance) if isinstance(confiance, (int, float)) else None
        else:
            valeur, citation, confiance = env, "", None
        verdicts[name] = judge_field(
            name, valeur, citation=citation, model_confidence=confiance,
            source=source, record=record, bulletin_date=bulletin_date, rules=rules,
        )

    # Score global : moyenne pondérée par la priorité, sur les seuls champs
    # renseignés. Y inclure les champs absents mêlerait deux questions —
    # « est-ce complet ? » et « est-ce juste ? » — qui n'ont pas le même remède.
    poids_total = 0.0
    cumul = 0.0
    for name, verdict in verdicts.items():
        if verdict.form_status == Status.MISSING.value:
            continue
        poids = float(int(profile.priority(name)))
        poids_total += poids
        cumul += poids * verdict.reliability
    score = (cumul / poids_total) if poids_total else 0.0

    jugement = Judgement(unit_id=unit_id, score=round(score, 3), fields=verdicts)
    jugement.alerts = _judge_alerts(jugement, profile, pages or [])
    return jugement


def _judge_alerts(jugement: Judgement, profile: FieldProfile, pages: list[int]) -> list[Alert]:
    alertes: list[Alert] = []
    for name, verdict in jugement.fields.items():
        if verdict.form_status == Status.MISSING.value or verdict.reliable:
            continue
        priorite = profile.priority(name)
        gravite = Severity.ERROR if priorite >= Priority.IMPORTANT else Severity.WARNING
        if verdict.anchored is False:
            code, message = (
                Code.CHAMP_NON_ANCRE,
                f"Fiabilité {verdict.percent} % pour « {name} » : citation introuvable dans la page",
            )
        else:
            code, message = (
                Code.CHAMP_SUSPECT,
                f"Fiabilité {verdict.percent} % pour « {name} »",
            )
        alertes.append(Alert(
            code=code, severity=gravite, message=message,
            page_refs=pages, unit_id=jugement.unit_id, field_name=name,
            hint=" ; ".join(verdict.criteria_failed[:2]) or (verdict.reasons[0] if verdict.reasons else ""),
            evidence=verdict.criteria_failed[:3],
        ))

    if not jugement.reliable:
        alertes.append(Alert(
            code=Code.EXTRACTION_ECHOUEE,
            severity=Severity.ERROR,
            message=f"Fiabilité globale {jugement.percent} % "
                    f"(seuil {SEUIL_FIABILITE_GLOBALE:.0%}) : relecture humaine requise",
            page_refs=pages, unit_id=jugement.unit_id,
            hint="Corriger dans le dashboard : la correction sera réinjectée dans le "
                 "prompt des extractions suivantes.",
            evidence=jugement.unreliable_fields[:6],
        ))
    return alertes
