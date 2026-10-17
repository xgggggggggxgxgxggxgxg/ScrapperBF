"""Tests hors-ligne du scoreur (moitié code pur du juge)."""
import scoreur

GOOD = {
    "autorite_emettrice": "COMMUNE DE OUAGADOUGOU",
    "objet": "Acquisition de matériel informatique au profit de la commune",
    "type_avis": "Avis de demande de prix",
    "numero_reference": "N° 2026-001/CO",
    "montant_previsionnel_global": {"ttc": "80 000 000"},
    "date_limite_depot": "15/11/2026 à 09h00",
    "est_en_lots": False,
}
TEXT_GOOD = ("Avis de demande de prix. Objet : acquisition de matériel informatique. "
             "Montant prévisionnel 80 000 000 FCFA. Garantie de soumission un million "
             "(1 000 000) FCFA. Les offres doivent parvenir avant le 15/11/2026 à 09h00. "
             "Lot unique. Le Président de la commission d'attribution des marchés.")


def test_good_avis_scores_high():
    r = scoreur.scorer_avis(GOOD, TEXT_GOOD)
    assert r["score"] >= 85
    assert r["verdict"] == "bon"
    assert r["champs_critiques_manquants"] == []


def test_missing_critical_field_flagged():
    champs = dict(GOOD)
    champs.pop("montant_previsionnel_global")
    r = scoreur.scorer_avis(champs, TEXT_GOOD)
    assert "montant_previsionnel_global" in r["champs_critiques_manquants"]
    assert r["verdict"] != "bon"  # un champ critique absent interdit le "bon"


def test_corrupted_text_scores_low():
    champs = {"objet": "x"}
    r = scoreur.scorer_avis(champs, "Lrtmnqs bcdfgh klmnpqrst vwxz tqrst zzz")
    assert r["verdict"] == "mauvais"


def test_severe_mode_is_stricter():
    champs = dict(GOOD)
    champs["montant_previsionnel_global"] = {}   # montant vide -> warn sur complétude
    normal = scoreur.scorer_avis(champs, TEXT_GOOD, severe=False)
    severe = scoreur.scorer_avis(champs, TEXT_GOOD, severe=True)
    assert severe["score"] <= normal["score"]
