"""Tests hors-ligne de la hiérarchie AVIS → sous-unités nommées."""
from sub_units import sub_unit_name, group_sub_units, _rank, MACRO_ORDER, CAT_ORDER


def test_sub_unit_name_canonical():
    assert sub_unit_name("REGIONS", "travaux", "N4503", "20261016") == \
        "avis-regions-travaux-N4503-20261016"
    assert sub_unit_name("MINISTERES", "prestations_intellectuelles", "N4503", "20261016") == \
        "avis-ministeres-prestations-intellectuelles-N4503-20261016"


def test_sub_unit_name_handles_unknown():
    assert sub_unit_name(None, None, "N1", "20261016") == "avis-autre-autre-N1-20261016"


def test_rank_orders_ministeres_before_regions_and_cats():
    order = sorted([("REGIONS", "fournitures"), ("MINISTERES", "prestations_intellectuelles"),
                    ("MINISTERES", "travaux"), ("REGIONS", "travaux")],
                   key=lambda k: _rank(*k))
    assert order == [("MINISTERES", "travaux"), ("MINISTERES", "prestations_intellectuelles"),
                     ("REGIONS", "travaux"), ("REGIONS", "fournitures")]


def test_group_sub_units_tags_and_orders():
    avis = [
        {"_macro_section": "REGIONS", "_categorie": "travaux", "_page_debut": 60, "_page_fin": 61,
         "_triplet_complet": True},
        {"_macro_section": "MINISTERES", "_categorie": "travaux", "_page_debut": 5, "_page_fin": 5,
         "_triplet_complet": True},
        {"_macro_section": "MINISTERES", "_categorie": "travaux", "_page_debut": 6, "_page_fin": 7,
         "_triplet_complet": False},  # triplet incomplet
    ]
    units = group_sub_units(avis, "N4503", "20261016")
    # ministères-travaux vient avant régions-travaux
    assert [u["nom"] for u in units] == [
        "avis-ministeres-travaux-N4503-20261016",
        "avis-regions-travaux-N4503-20261016",
    ]
    mt = units[0]
    assert mt["nb_annonces"] == 2
    assert mt["pages"] == [5, 7]
    assert mt["nb_triplet_incomplet"] == 1
    # chaque annonce est taguée de sa sous-unité
    assert avis[1]["_sous_unite"] == "avis-ministeres-travaux-N4503-20261016"
    assert avis[0]["_sous_unite"] == "avis-regions-travaux-N4503-20261016"
