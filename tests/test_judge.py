"""Tests hors-ligne du juge (vérificateur de cohérence déterministe)."""
from judge import (
    parse_amounts, garbling_score, declared_lot_count, has_date,
    judge_avis, judge_resultats, montant_amounts,
)


# --- primitives ------------------------------------------------------------

def test_parse_amounts_groups_and_excludes_years():
    a = parse_amounts("quatre-vingt millions (80 000 000) F CFA en 2026, soit 60 000 000 TTC")
    assert 80000000 in a and 60000000 in a
    assert 2026 not in a


def test_garbling_low_for_clean_text():
    assert garbling_score("Acquisition de matériel au profit de la commune") < 0.1


def test_garbling_high_for_corrupted():
    assert garbling_score("Lrtmnqs bcdfgh klmnpqrst vwxz tqrst") > 0.5


def test_declared_lot_count():
    assert declared_lot_count("Les acquisitions se décomposent en un lot unique") == 1
    assert declared_lot_count("Le marché est divisé en trois (03) lots") == 3
    assert declared_lot_count("Lot 1 : x\nLot 2 : y\nLot 3 : z") == 3
    assert declared_lot_count("aucune mention de lot") is None


def test_has_date():
    assert has_date("avant le 24/08/2026 à 09 heures")
    assert has_date("le 15 novembre 2026")
    assert not has_date("prochainement")


# --- judge_avis ------------------------------------------------------------

COMPLET = {
    "autorite_emettrice": "COMMUNE DE MANSILA", "objet": "Acquisition de matériel divers",
    "type_avis": "Avis de demande de prix", "numero_reference": "N° 2026-01/RLTP/PYGH/CMSL",
    "montant_previsionnel_global": {"ttc": "quatre-vingt millions (80 000 000) F CFA TTC"},
    "date_limite_depot": "avant le 24/08/2026 à 09 heures",
}
TEXTE = ("Avis de demande de prix. Montant prévisionnel : (80 000 000) TTC. "
         "Les acquisitions se décomposent en un lot unique. "
         "garantie de soumission d'un montant de un million (1 000 000) FCFA. "
         "devront parvenir avant le 24/08/2026 à 09 heures. "
         "Le Président de la Commission d'attribution des marchés KOUNIO Roland")


def test_complete_avis_is_valid():
    j = judge_avis(COMPLET, TEXTE)
    assert j["verdict"] == "valide"
    assert j["confiance"] >= 0.9
    assert j["revue_humaine"] is False


def test_missing_deadline_flags_review():
    a = dict(COMPLET); a["date_limite_depot"] = None
    j = judge_avis(a, TEXTE.replace("avant le 24/08/2026 à 09 heures", ""))
    assert j["verdict"] != "valide"
    noms = {c["nom"] for c in j["anomalies"]}
    assert "present:date_limite_depot" in noms


def test_implausible_amount_fails_check():
    a = dict(COMPLET); a["montant_previsionnel_global"] = {"ttc": "cinq cents (500) FCFA"}
    j = judge_avis(a, TEXTE)
    noms = {c["nom"]: c["statut"] for c in j["checks"]}
    assert noms["montant_plausible"] == "fail"


def test_caution_ratio_warn_when_off():
    # caution = 40 000 000 sur budget 80 000 000 = 50% -> incohérent
    txt = TEXTE.replace("un million (1 000 000)", "quarante millions (40 000 000)")
    j = judge_avis(COMPLET, txt)
    ratio = next(c for c in j["checks"] if c["nom"] == "ratio_caution")
    assert ratio["statut"] == "warn"


def test_caution_ratio_pass_when_standard():
    j = judge_avis(COMPLET, TEXTE)   # 1 000 000 / 80 000 000 = 1.25%
    ratio = next(c for c in j["checks"] if c["nom"] == "ratio_caution")
    assert ratio["statut"] == "pass"


def test_corrupted_text_lowers_confidence():
    j = judge_avis(COMPLET, "Lrtmnqs bcdfgh klmnpqrst vwxz tqrst zzz")
    lis = next(c for c in j["checks"] if c["nom"] == "texte_lisible")
    assert lis["statut"] in ("warn", "fail")


def test_truncated_avis_without_signature_warns():
    j = judge_avis(COMPLET, "Avis de demande de prix. Montant (80 000 000). Les offres")
    sig = next(c for c in j["checks"] if c["nom"] == "cloture_signature")
    assert sig["statut"] == "warn"


# --- judge_resultats -------------------------------------------------------

def test_resultats_lot_coherence_and_attributaire():
    res = {"autorite_contractante": "MINISTERE X", "nombre_lots": 2, "lots": [
        {"numero": 1, "soumissionnaires": [{"nom": "A"}], "attributaire_provisoire": {"nom": "A"}},
        {"numero": 2, "soumissionnaires": [{"nom": "B"}], "infructueux": True},
    ]}
    j = judge_resultats(res, "Le marché est divisé en deux (02) lots")
    assert j["confiance"] >= 0.9
    att = next(c for c in j["checks"] if c["nom"] == "attributaires")
    assert att["statut"] == "pass"


def test_resultats_lot_mismatch_warns():
    res = {"autorite_contractante": "MIN X", "nombre_lots": 1, "lots": [{"numero": 1, "soumissionnaires": []}]}
    j = judge_resultats(res, "divisé en trois (03) lots")
    lc = next(c for c in j["checks"] if c["nom"] == "lots_coherents")
    assert lc["statut"] == "warn"
