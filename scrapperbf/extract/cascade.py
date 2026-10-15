"""Cascade d'extraction : du gratuit au coûteux, et jamais l'inverse.

L'ordre est une décision économique autant que technique. Chaque marche ne
s'exécute que si la précédente n'a pas suffi :

======  ==============================  =========================================
Marche  Moyen                           Coût
======  ==============================  =========================================
1       contrôle d'intégrité de la page gratuit, et évite de dépenser pour rien
2       extraction par motifs (regex)   gratuit, reproductible, justifiable
3       modèle de décision              gratuit : estime si les valeurs tiennent
4       relecture Light OCR             quelques secondes sur la seule page en
                                        cause, quand le texte intégré est douteux
5       appel LLM                       facturé — et limité aux champs manquants
6       alerte administrateur           du temps humain, le plus précieux
======  ==============================  =========================================

Le **modèle de décision** (``decide``) est une fonction pure : mêmes entrées,
même décision, testable sans réseau. Elle répond à une seule question — « ce
qu'on a en main se tient-il, et sinon que tenter ensuite ? » — et c'est elle
qui porte le seuil de 75 % d'informations essentielles en dessous duquel un
humain doit être prévenu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ..core.priority import FieldProfile, Priority
from ..qa.alerts import Alert, Code, Severity
from ..qa.integrity import IntegrityReport
from ..qa.judge import SEUIL_FIABILITE_GLOBALE as RELIABILITY_THRESHOLD
from ..qa.rules import Status, check_field
from .labels import Extracted, extract_all

# Part minimale des informations essentielles (champs critiques et importants,
# pondérés par leur priorité) en dessous de laquelle l'annonce ne peut pas être
# publiée sans relecture humaine.
ESSENTIAL_THRESHOLD = 0.75


class Step(str, Enum):
    """Marches franchies, dans l'ordre."""

    INTEGRITE = "controle_integrite"
    REGEX = "extraction_motifs"
    DECISION = "modele_decision"
    OCR = "relecture_ocr"
    LLM = "appel_llm"
    ALERTE = "alerte_administrateur"


class Decision(str, Enum):
    """Suite à donner après évaluation de ce qui a été extrait."""

    ACCEPTER = "accepter"
    RELIRE_OCR = "relire_ocr"
    COMPLETER_LLM = "completer_llm"
    ALERTER = "alerter_administrateur"


@dataclass
class Evaluation:
    """Ce que le modèle de décision a constaté."""

    coverage: float
    decision: Decision
    missing_essential: list[str] = field(default_factory=list)
    doubtful: dict[str, str] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "coverage": round(self.coverage, 3),
            "decision": self.decision.value,
            "missing_essential": self.missing_essential,
            "doubtful": self.doubtful,
            "reasons": self.reasons,
        }


@dataclass
class CascadeResult:
    """Résultat complet, avec la trace des marches franchies."""

    unit_id: str
    fields: dict[str, Extracted] = field(default_factory=dict)
    lots: list[dict] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    evaluations: list[Evaluation] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    integrity: IntegrityReport | None = None
    reliability: float | None = None

    @property
    def record(self) -> dict:
        out = {name: e.value for name, e in self.fields.items()}
        if self.lots:
            out["lots"] = self.lots
            out["est_en_lots"] = len(self.lots) > 1
        return out

    @property
    def final(self) -> Evaluation | None:
        return self.evaluations[-1] if self.evaluations else None

    @property
    def needs_admin(self) -> bool:
        return self.final is not None and self.final.decision is Decision.ALERTER

    @property
    def llm_called(self) -> bool:
        return Step.LLM in self.steps

    def to_dict(self) -> dict:
        return {
            "unit_id": self.unit_id,
            "record": self.record,
            "provenance": {n: e.to_dict() for n, e in self.fields.items()},
            "steps": [s.value for s in self.steps],
            "evaluations": [e.to_dict() for e in self.evaluations],
            "alerts": [a.to_dict() for a in self.alerts],
            "integrity": self.integrity.to_dict() if self.integrity else None,
            "needs_admin": self.needs_admin,
            "llm_called": self.llm_called,
            "reliability": round(self.reliability, 3) if self.reliability is not None else None,
            "reliability_percent": round(self.reliability * 100) if self.reliability is not None else None,
        }


def essential_fields(profile: FieldProfile) -> dict[str, int]:
    """Champs essentiels et leur poids : critiques et importants seulement.

    Les champs optionnels sont exclus du calcul : les compter ferait chuter la
    couverture d'une annonce pourtant complète, et noierait le seuil.
    """
    return {
        name: int(priority)
        for name, priority in profile.priorities.items()
        if priority >= Priority.IMPORTANT
    }


def essential_coverage(record: dict, profile: FieldProfile) -> tuple[float, list[str]]:
    """Part pondérée des informations essentielles effectivement présentes."""
    poids = essential_fields(profile)
    if not poids:
        return 1.0, []
    total = sum(poids.values())
    acquis = 0
    manquants = []
    for name, poids_champ in poids.items():
        valeur = record.get(name)
        if check_field(name, valeur).status is Status.MISSING:
            manquants.append(name)
        else:
            acquis += poids_champ
    return acquis / total, sorted(manquants)


def doubtful_fields(record: dict, profile: FieldProfile, source_text: str = "") -> dict[str, str]:
    """Champs renseignés mais douteux : forme, plausibilité ou ancrage.

    Ce sont eux qui déclenchent une relecture OCR : une valeur présente mais
    aberrante vient plus souvent d'un texte mal reconnu que d'un mauvais motif.
    """
    out: dict[str, str] = {}
    for name in essential_fields(profile):
        valeur = record.get(name)
        verdict = check_field(name, valeur, source_text)
        if verdict.status in (Status.SUSPECT, Status.INVALID, Status.UNGROUNDED):
            out[name] = f"{verdict.status.value}: {verdict.reason}"
    return out


def decide(
    coverage: float,
    doubtful: dict[str, str],
    *,
    integrity: IntegrityReport | None = None,
    ocr_done: bool = False,
    llm_done: bool = False,
    ocr_available: bool = True,
    llm_available: bool = True,
    reliability: float | None = None,
) -> Evaluation:
    """Modèle de décision : que faire de ce qui a été extrait.

    Fonction pure, sans effet de bord : c'est elle qu'on teste pour savoir
    comment le système se comportera, plutôt que d'exécuter la cascade entière.
    """
    reasons: list[str] = []
    assez = coverage >= ESSENTIAL_THRESHOLD

    if integrity is not None and not integrity.safe:
        reasons.extend(f"intégrité : {p}" for p in integrity.problems)

    # Le score du juge, quand il est connu, est un verdict sur la JUSTESSE de
    # ce qui a été extrait, là où la couverture ne dit que la quantité. Une
    # extraction complète mais peu fiable ne doit pas être acceptée.
    if reliability is not None and reliability < RELIABILITY_THRESHOLD:
        reasons.append(
            f"fiabilité jugée à {reliability:.0%}, sous le seuil de {RELIABILITY_THRESHOLD:.0%}"
        )
        if not ocr_done and ocr_available:
            reasons.append("relire la page en OCR avant d'interroger le modèle")
            return Evaluation(coverage, Decision.RELIRE_OCR, [], doubtful, reasons)
        if not llm_done and llm_available:
            return Evaluation(coverage, Decision.COMPLETER_LLM, [], doubtful, reasons)
        return Evaluation(coverage, Decision.ALERTER, [], doubtful, reasons)

    if assez and not doubtful:
        if integrity is not None and not integrity.safe:
            # Les valeurs tiennent, mais la page est douteuse : un humain doit
            # confirmer, car un mélange d'annonces produit des valeurs
            # parfaitement bien formées… et attribuées au mauvais marché.
            reasons.append("valeurs complètes et plausibles, mais page non fiable")
            return Evaluation(coverage, Decision.ALERTER, [], doubtful, reasons)
        reasons.append(f"{coverage:.0%} des informations essentielles, aucune valeur douteuse")
        return Evaluation(coverage, Decision.ACCEPTER, [], doubtful, reasons)

    if doubtful and not ocr_done and ocr_available:
        reasons.append(f"{len(doubtful)} valeur(s) douteuse(s) : relire la page en OCR")
        return Evaluation(coverage, Decision.RELIRE_OCR, [], doubtful, reasons)

    if not assez and not llm_done and llm_available:
        reasons.append(f"{coverage:.0%} < {ESSENTIAL_THRESHOLD:.0%} : compléter par le modèle")
        return Evaluation(coverage, Decision.COMPLETER_LLM, [], doubtful, reasons)

    if assez and doubtful and not llm_done and llm_available:
        reasons.append("valeurs douteuses persistantes après relecture : arbitrage du modèle")
        return Evaluation(coverage, Decision.COMPLETER_LLM, [], doubtful, reasons)

    if assez and not doubtful:
        reasons.append("informations complètes après les étapes précédentes")
        return Evaluation(coverage, Decision.ACCEPTER, [], doubtful, reasons)

    if not assez:
        reasons.append(f"{coverage:.0%} < {ESSENTIAL_THRESHOLD:.0%} après toutes les tentatives")
    else:
        reasons.append("valeurs douteuses non levées après toutes les tentatives")
    return Evaluation(coverage, Decision.ALERTER, [], doubtful, reasons)


def run_cascade(
    unit_id: str,
    body: str,
    profile: FieldProfile,
    *,
    header: dict[str, object] | None = None,
    integrity: IntegrityReport | None = None,
    reread_ocr: Callable[[], str] | None = None,
    ask_llm: Callable[[str, list[str]], dict] | None = None,
    rate: Callable[[dict, str], float] | None = None,
) -> CascadeResult:
    """Exécute la cascade sur une annonce.

    ``rate`` est le juge : il reçoit l'enregistrement et le texte source, et
    rend un score de fiabilité entre 0 et 1 (cf. ``qa.judge.judge``). Sans lui,
    la décision ne porte que sur la couverture et la forme des valeurs.

    ``reread_ocr`` relit les pages de l'annonce par OCR et retourne le texte ;
    ``ask_llm`` reçoit le texte et la liste des champs encore manquants, et ne
    doit renseigner que ceux-là — demander au modèle ce qu'on possède déjà, on
    le paierait pour rien tout en risquant d'écraser une valeur plus sûre.
    """
    resultat = CascadeResult(unit_id=unit_id, integrity=integrity)
    resultat.steps.append(Step.INTEGRITE)
    if integrity is not None:
        resultat.alerts.extend(integrity.alerts)

    texte = body
    extraction = extract_all(texte, header)
    resultat.fields = dict(extraction.fields)
    resultat.lots = list(extraction.lots)
    resultat.steps.append(Step.REGEX)

    ocr_done = llm_done = False
    for _ in range(3):  # intégrité → regex → ocr → llm : trois arbitrages au plus
        resultat.steps.append(Step.DECISION)
        coverage, manquants = essential_coverage(resultat.record, profile)
        doutes = doubtful_fields(resultat.record, profile, texte)
        fiabilite = None
        if rate is not None:
            try:
                fiabilite = rate(resultat.record, texte)
            except Exception as exc:
                resultat.alerts.append(Alert(
                    code=Code.EXTRACTION_ECHOUEE, severity=Severity.WARNING,
                    message=f"Notation de fiabilité impossible : {exc}",
                    page_refs=integrity.pages if integrity else [], unit_id=unit_id,
                    hint="La décision se fonde alors sur la seule couverture.",
                ))
        resultat.reliability = fiabilite
        evaluation = decide(
            coverage, doutes, integrity=integrity,
            ocr_done=ocr_done, llm_done=llm_done,
            ocr_available=reread_ocr is not None, llm_available=ask_llm is not None,
            reliability=fiabilite,
        )
        evaluation.missing_essential = manquants
        resultat.evaluations.append(evaluation)

        if evaluation.decision is Decision.ACCEPTER:
            return resultat

        if evaluation.decision is Decision.RELIRE_OCR and reread_ocr is not None:
            resultat.steps.append(Step.OCR)
            ocr_done = True
            try:
                relu = reread_ocr()
            except Exception as exc:
                resultat.alerts.append(Alert(
                    code=Code.OCR_DEGRADE, severity=Severity.WARNING,
                    message=f"Relecture OCR impossible : {exc}",
                    page_refs=integrity.pages if integrity else [], unit_id=unit_id,
                    hint="La cascade continue sur le texte intégré.",
                ))
                continue
            if relu and relu.strip():
                texte = relu
                # La relecture ne remplace que ce qu'elle lit mieux : une valeur
                # venue de la maquette ou d'une étiquette reste prioritaire.
                nouvelle = extract_all(texte, header)
                for name, extracted in nouvelle.fields.items():
                    ancienne = resultat.fields.get(name)
                    if ancienne is None or extracted.confidence > ancienne.confidence:
                        extracted.source = f"{extracted.source}+ocr"
                        resultat.fields[name] = extracted
                if nouvelle.lots and not resultat.lots:
                    resultat.lots = nouvelle.lots
            continue

        if evaluation.decision is Decision.COMPLETER_LLM and ask_llm is not None:
            resultat.steps.append(Step.LLM)
            llm_done = True
            cibles = sorted(set(evaluation.missing_essential) | set(evaluation.doubtful))
            try:
                reponse = ask_llm(texte, cibles) or {}
            except Exception as exc:
                resultat.alerts.append(Alert(
                    code=Code.EXTRACTION_ECHOUEE, severity=Severity.ERROR,
                    message=f"Appel au modèle en échec : {exc}",
                    page_refs=integrity.pages if integrity else [], unit_id=unit_id,
                    hint="Les valeurs déjà extraites par motifs sont conservées.",
                ))
                continue
            for name in cibles:
                valeur = reponse.get(name)
                if valeur in (None, "", [], {}):
                    continue
                ancienne = resultat.fields.get(name)
                # Le modèle ne prend la place d'une valeur existante que si
                # celle-ci était douteuse : sinon il dégraderait une certitude.
                if ancienne is None or name in evaluation.doubtful:
                    resultat.fields[name] = Extracted(
                        field=name, value=valeur, source="llm",
                        evidence="réponse du modèle", confidence=0.75,
                    )
            continue

        break

    resultat.steps.append(Step.ALERTE)
    finale = resultat.final
    coverage = finale.coverage if finale else 0.0
    resultat.alerts.append(Alert(
        code=Code.EXTRACTION_ECHOUEE,
        severity=Severity.ERROR if coverage < ESSENTIAL_THRESHOLD else Severity.WARNING,
        message=f"Seulement {coverage:.0%} des informations essentielles obtenues "
                f"(seuil {ESSENTIAL_THRESHOLD:.0%})",
        page_refs=integrity.pages if integrity else [],
        unit_id=unit_id,
        hint="Relecture humaine requise : corriger dans le dashboard, la correction "
             "sera réinjectée dans le prompt des extractions suivantes.",
        evidence=(finale.missing_essential if finale else [])[:8],
    ))
    return resultat
