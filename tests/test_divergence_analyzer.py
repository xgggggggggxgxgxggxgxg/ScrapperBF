"""Tests hors-ligne de l'analyse déterministe des divergences A vs B."""
from divergence_analyzer import classify_field, analyze_divergence

FIELDS = ["autorite_emettrice", "objet", "date_limite_depot",
          "date_ouverture_plis", "montant_previsionnel_global", "numero_reference"]


def test_no_divergence_when_equal():
    a = {"objet": "Acquisition de PC"}
    b = {"objet": "acquisition de  pc"}      # casse/espaces
    assert classify_field("objet", a, b, FIELDS) is None


def test_champ_vide():
    a = {"numero_reference": "N°2026-1"}
    b = {"numero_reference": ""}
    d = classify_field("numero_reference", a, b, FIELDS)
    assert d["cause"] == "champ_vide" and d["gravite"] == 1


def test_echange_de_champs():
    # A et B ont interverti date_limite et date_ouverture
    a = {"date_limite_depot": "19/10/2026", "date_ouverture_plis": "20/10/2026"}
    b = {"date_limite_depot": "20/10/2026", "date_ouverture_plis": "19/10/2026"}
    d = classify_field("date_limite_depot", a, b, FIELDS)
    assert d["cause"] == "echange_de_champs"
    assert "date_ouverture_plis" in d["explication"]


def test_htva_ttc_not_a_contradiction():
    a = {"montant_previsionnel_global": "(50 000 000)"}
    b = {"montant_previsionnel_global": "(60 000 000)"}
    txt = "montant brut (50 000 000) HTVA soit (60 000 000) TTC"
    d = classify_field("montant_previsionnel_global", a, b, FIELDS, txt)
    assert d["cause"] == "htva_ttc" and d["gravite"] == 1


def test_montant_contradiction_without_tax_mention():
    a = {"montant_previsionnel_global": "(50 000 000)"}
    b = {"montant_previsionnel_global": "(90 000 000)"}
    d = classify_field("montant_previsionnel_global", a, b, FIELDS, "aucune mention de taxe")
    assert d["cause"] == "contradiction" and d["gravite"] == 3


def test_date_same_day_different_format():
    a = {"date_limite_depot": "19/10/2026"}
    b = {"date_limite_depot": "19-10-2026"}
    d = classify_field("date_limite_depot", a, b, FIELDS)
    assert d["cause"] == "valeur_proche"


def test_real_contradiction_triggers_human_review():
    a = {"autorite_emettrice": "MINISTERE DE LA SANTE", "objet": "X"}
    b = {"autorite_emettrice": "COMMUNE DE OUAGA", "objet": "X"}
    res = analyze_divergence(a, b, FIELDS, "")
    assert res["n_contradictions"] == 1
    assert res["alerte_humaine"] is True              # champ durable
    assert any("autorite_emettrice" in r for r in res["raisons"])


def test_format_only_divergences_no_alert():
    a = {"objet": "Acquisition de PC", "numero_reference": "N°1"}
    b = {"objet": "acquisition de pc", "numero_reference": "N°1"}
    res = analyze_divergence(a, b, FIELDS, "")
    assert res["divergences"] == []
    assert res["alerte_humaine"] is False
