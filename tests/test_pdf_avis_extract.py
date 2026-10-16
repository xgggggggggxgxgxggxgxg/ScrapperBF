"""Tests hors-ligne de la segmentation AVIS (plus d'extraction regex des champs)."""
from pdf_avis_extract import (
    enrich_coverage, CORE_FIELDS, TYPE_HEAD_RE, _numero_label,
    segment_avis_blocks, extract_avis_from_pdf, norm,
    _detect_category, _macro_from_emetteur, MACRO_MINISTERES, MACRO_REGIONS,
    is_red, is_yellow, is_cyan, classify_band_color, _category_label, plan_annonces,
)


def test_type_head_recognizes_annonce_headers():
    assert TYPE_HEAD_RE.match("Avis de demande de prix")
    assert TYPE_HEAD_RE.match("AVIS D'APPEL D'OFFRES OUVERT")
    assert TYPE_HEAD_RE.match("Avis à manifestation d'intérêt")
    assert not TYPE_HEAD_RE.match("Le présent marché concerne l'acquisition")


def test_numero_label_from_filename():
    assert _numero_label("x/Quotidien_N_4503.pdf", None, None) == ("N4503", "Quotidien N°4503")
    assert _numero_label("x/Quotidien_n_4503bis.pdf", None, None) == ("N4503bis", "Quotidien N°4503bis")


def test_extract_is_alias_of_segment():
    assert extract_avis_from_pdf is segment_avis_blocks


def test_norm_collapses_whitespace():
    assert norm("  a   b\n c ") == "a b c"


def test_enrich_coverage_full():
    a = {k: "x" for k in CORE_FIELDS}
    enrich_coverage(a)
    assert a["_score_exactitude"] == 100
    assert a["_champs_a_verifier"] == []
    assert len(a["_analyse"]) == len(CORE_FIELDS)


def test_enrich_coverage_partial_flags_missing():
    a = {"autorite_emettrice": "X", "objet": "Y", "type_avis": "Z"}  # 3/6
    enrich_coverage(a)
    assert a["_score_exactitude"] == 50
    assert set(a["_champs_a_verifier"]) == {"numero_reference", "financement", "date_limite_depot"}


def test_enrich_coverage_empty_block():
    a = {"flat_text": "..."}  # un bloc sans champ (avant extraction LLM)
    enrich_coverage(a)
    assert a["_score_exactitude"] == 0
    assert set(a["_champs_a_verifier"]) == set(CORE_FIELDS)


# --- Taxonomie hiérarchique (niveau 2 : catégorie, niveau 1 : macro) --------

def test_detect_category_bands():
    assert _detect_category("Travaux") == "travaux"
    assert _detect_category("Fournitures et Services courants") == "fournitures"
    assert _detect_category("Prestations intellectuelles") == "prestations_intellectuelles"
    assert _detect_category("Services de consultants") == "prestations_intellectuelles"
    # une ligne de corps d'annonce n'est PAS un bandeau de catégorie
    assert _detect_category("Le présent marché porte sur des travaux de construction") is None
    assert _detect_category("") is None


def test_macro_from_emetteur_switches_to_regions():
    assert _macro_from_emetteur("MINISTERE DE L'ECONOMIE", MACRO_MINISTERES) == MACRO_MINISTERES
    assert _macro_from_emetteur("REGION DU CENTRE", MACRO_MINISTERES) == MACRO_REGIONS
    assert _macro_from_emetteur("COMMUNE DE OUAGADOUGOU", MACRO_MINISTERES) == MACRO_REGIONS
    # bascule irréversible : un émetteur ministère après les régions ne revient pas
    assert _macro_from_emetteur("MINISTERE DE LA SANTE", MACRO_REGIONS) == MACRO_REGIONS


# --- Détection par couleur des bandeaux (rouge/jaune/cyan) -------------------

def test_color_predicates():
    assert is_red((0.9, 0.1, 0.1))        # bandeau catégorie
    assert is_yellow((1.0, 1.0, 0.0))     # bandeau émetteur
    assert is_cyan((0.0, 0.9, 0.9))       # bandeau objet
    # pas de confusion croisée
    assert not is_red((1.0, 1.0, 0.0)) and not is_cyan((1.0, 1.0, 0.0))
    assert not is_yellow((0.0, 0.9, 0.9))
    # couleur neutre / format invalide
    assert classify_band_color((0.5, 0.5, 0.5)) is None
    assert classify_band_color(0.0) is None  # scalaire (barre noire RÉSULTATS)
    assert classify_band_color(None) is None


def test_classify_band_color_roles():
    assert classify_band_color((0.95, 0.15, 0.12)) == "categorie"
    assert classify_band_color((0.98, 0.95, 0.05)) == "emetteur"
    assert classify_band_color((0.1, 0.85, 0.9)) == "objet"


def test_category_label_from_band_text():
    assert _category_label("Fournitures et Services courants") == "fournitures"
    assert _category_label("Travaux") == "travaux"
    assert _category_label("Prestations intellectuelles") == "prestations_intellectuelles"
    assert _category_label("Services de consultant") == "prestations_intellectuelles"
    assert _category_label("MINISTERE DE L'ECONOMIE") is None
    # tolérance aux fautes réelles du bulletin (« Furnitures » sans « o »)
    assert _category_label("Furnitures et Services courants") == "fournitures"
    assert _category_label("Fourntures") == "fournitures"


def test_plan_annonces_inherits_context():
    # Reproduit l'image : rouge(catégorie) > jaune(émetteur) > cyan(objet),
    # puis un 2e objet sous le même émetteur, puis une région.
    bands = [
        {"kind": "categorie", "text": "Fournitures et Services courants", "page": 0, "top": 10},
        {"kind": "emetteur", "text": "MINISTERE DE L'ECONOMIE ET DES FINANCES", "page": 0, "top": 30},
        {"kind": "objet", "text": "Entretien et maintenance de véhicules (2 lots)", "page": 0, "top": 50},
        {"kind": "objet", "text": "Acquisition de fournitures de bureau", "page": 1, "top": 20},
        {"kind": "emetteur", "text": "REGION DU CENTRE", "page": 2, "top": 10},
        {"kind": "objet", "text": "Construction d'un marché", "page": 2, "top": 40},
    ]
    plan = plan_annonces(bands)
    assert len(plan) == 3
    a, b, c = plan
    assert a["categorie"] == "fournitures"
    assert a["emetteur"].startswith("MINISTERE")
    assert a["macro_section"] == MACRO_MINISTERES
    assert a["nb_lots_declares"] == 2
    # 2e objet : hérite du même émetteur + catégorie, pas de lots déclarés
    assert b["emetteur"].startswith("MINISTERE") and b["categorie"] == "fournitures"
    assert b["nb_lots_declares"] is None
    # 3e objet : émetteur région -> macro bascule
    assert c["macro_section"] == MACRO_REGIONS and c["emetteur"] == "REGION DU CENTRE"
    # triplet complet (rouge + jaune vus avant le cyan) pour les trois
    assert a["triplet_complet"] and b["triplet_complet"] and c["triplet_complet"]


def test_plan_annonces_flags_incomplete_triplet():
    # un titre cyan SANS catégorie rouge ni émetteur jaune au-dessus
    bands = [{"kind": "objet", "text": "Acquisition de PC", "page": 0, "top": 10}]
    (a,) = plan_annonces(bands)
    assert a["triplet_complet"] is False
    assert set(a["contexte_manquant"]) == {"categorie", "emetteur"}
