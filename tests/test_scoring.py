from scrapperbf.core.priority import profile_for
from scrapperbf.core.titles import TitleKind
from scrapperbf.qa.alerts import Code, Severity
from scrapperbf.qa.rules import Status
from scrapperbf.qa.scoring import Verdict, field_alerts, score_record

SOURCE = (
    "Avis de demande de prix N°2026-012/MEF/SG/DMP pour l'acquisition de materiel informatique. "
    "Montant previsionnel 25 000 000 F CFA TTC. Offres recues le 14 aout 2026 a 09 heures 00, "
    "ouverture le 14 aout 2026 a 09 heures 30. Validite des offres 90 jours."
)
PROFILE = profile_for(TitleKind.DEMANDE_PRIX)


def test_extraction_complete_est_validee(extraction_demande_prix):
    score, _ = score_record(extraction_demande_prix, PROFILE, SOURCE,
                            title_confidence=0.9, span_confidence=0.95)
    assert score.verdict is Verdict.VALIDE
    assert score.accuracy == 1.0 and score.confidence > 0.85


def test_champ_critique_absent_interdit_le_verdict_valide(extraction_demande_prix):
    record = dict(extraction_demande_prix)
    record.pop("date_limite_depot")
    score, _ = score_record(record, PROFILE, SOURCE, title_confidence=0.95, span_confidence=0.95)
    assert score.verdict is not Verdict.VALIDE
    assert score.missing_critical == ["date_limite_depot"]


def test_plusieurs_champs_critiques_absents_mene_au_rejet(extraction_demande_prix):
    record = dict(extraction_demande_prix)
    for field in ("date_limite_depot", "numero_reference"):
        record.pop(field)
    score, _ = score_record(record, PROFILE, SOURCE)
    assert score.verdict is Verdict.REJETE


def test_extraction_vide_ne_recoit_pas_une_bonne_note():
    score, _ = score_record({}, PROFILE, SOURCE)
    assert score.accuracy == 0.0 and score.verdict is Verdict.REJETE


def test_valeur_hallucinee_fait_chuter_l_exactitude(extraction_demande_prix):
    record = dict(extraction_demande_prix)
    record["montant_previsionnel_global"] = "99 999 999 F CFA"
    score, checks = score_record(record, PROFILE, SOURCE, title_confidence=0.9, span_confidence=0.95)
    assert checks["montant_previsionnel_global"].status is Status.UNGROUNDED
    assert score.accuracy < 1.0 and score.verdict is not Verdict.VALIDE


def test_lots_declares_sans_detail_est_incoherent(extraction_demande_prix):
    record = dict(extraction_demande_prix, est_en_lots=True, lots=[])
    _, checks = score_record(record, PROFILE, SOURCE)
    assert checks["lots"].status is Status.INVALID


def test_montants_min_max_inverses_sont_detectes():
    profile = profile_for(TitleKind.RESULTATS)
    record = {
        "autorite_contractante": "Region du Centre-Nord",
        "objet": "Entretien des voiries urbaines",
        "attributaire_provisoire": "ENTREPRISE ALPHA",
        "montant_attribue_min": "20 000 000",
        "montant_attribue_max": "10 000 000",
    }
    _, checks = score_record(record, profile, "montants 20 000 000 et 10 000 000")
    assert checks["montant_attribue_max"].status is Status.INVALID


def test_ouverture_avant_date_limite_est_impossible(extraction_demande_prix):
    record = dict(extraction_demande_prix)
    record["date_ouverture_plis"] = "01 aout 2026 a 09 heures 00"
    _, checks = score_record(record, PROFILE, SOURCE + " 01 aout 2026")
    assert checks["date_ouverture_plis"].status is Status.INVALID


def test_structure_degradee_par_un_ocr_faible(extraction_demande_prix):
    bonne, _ = score_record(extraction_demande_prix, PROFILE, SOURCE, ocr_confidence=1.0)
    mauvaise, _ = score_record(extraction_demande_prix, PROFILE, SOURCE, ocr_confidence=0.4)
    assert mauvaise.structure < bonne.structure
    assert any("reconnaissance de texte" in r for r in mauvaise.reasons)


def test_les_alertes_de_champ_sont_graduees_par_priorite(extraction_demande_prix):
    record = dict(extraction_demande_prix)
    record.pop("date_limite_depot")       # critique
    record.pop("financement")             # important
    record.pop("type_avis")               # optionnel
    _, checks = score_record(record, PROFILE, SOURCE)
    alerts = field_alerts(checks, PROFILE, "AVIS#1", [12])
    codes = {a.field_name: (a.code, a.severity) for a in alerts}
    assert codes["date_limite_depot"] == (Code.CHAMP_CRITIQUE_MANQUANT, Severity.ERROR)
    assert codes["financement"] == (Code.CHAMP_IMPORTANT_MANQUANT, Severity.WARNING)
    # Un champ optionnel absent n'est pas un événement : il ne crie pas.
    assert "type_avis" not in codes


def test_les_alertes_portent_les_references_de_page(extraction_demande_prix):
    record = dict(extraction_demande_prix)
    record.pop("objet")
    _, checks = score_record(record, PROFILE, SOURCE)
    alerts = field_alerts(checks, PROFILE, "AVIS#1", [12, 13])
    assert alerts and all(a.page_refs == [12, 13] for a in alerts)
    assert "p. 12–13" in alerts[0].human()
