"""Tests hors-ligne du garde-fou format."""
from format_validator import (
    page_range, count_annonce_headers, table_bad_data, split_double, validate_format,
)

HDR = "Avis de demande de prix n° : 2026-1/CMN/DMP"
CLEAN = (f"Fournitures et Services courants COMMUNE DE X Acquisition de matériel "
         f"{HDR} Financement : Budget. devront parvenir avant le 24/08/2026 à 09 heures. "
         f"Le Président de la Commission d'attribution")


def test_page_range():
    assert page_range(3, 3) == "3"
    assert page_range(3, 5) == "3-5"
    assert page_range(7, None) == "7"


def test_count_headers():
    assert count_annonce_headers(CLEAN) == 1
    assert count_annonce_headers(CLEAN + " " + CLEAN) == 2
    assert count_annonce_headers("aucun en-tête ici") == 0


def test_table_bad_data():
    assert table_bad_data("A -- -- B -- -- C -- -- Lrtmnqs bcdfgh klmnpqrst vwxz") is True
    assert table_bad_data(CLEAN) is False


def test_split_double():
    parts = split_double(CLEAN + " " + CLEAN)
    assert len(parts) == 2


def test_validate_clean_single():
    r = validate_format({"flat_text": CLEAN, "_page_debut": 3, "_page_fin": 3})
    assert r["action"] == "extraire" and r["valide"] is True
    assert r["pages"] == "3" and r["alerte_humaine"] is False


def test_validate_double_triggers_split():
    r = validate_format({"flat_text": CLEAN + " " + CLEAN, "_page_debut": 3, "_page_fin": 3})
    assert r["action"] == "diviser"
    assert r["nb_annonces"] == 2 and len(r["sous_annonces"]) == 2


def test_validate_corrupted_blocks():
    r = validate_format({"flat_text": "Lrtmnqs bcdfgh klmnpqrst vwxz tqrst zzz wxyz bcdfg",
                         "_page_debut": 4, "_page_fin": 4})
    assert r["action"] == "bloquer" and r["alerte_humaine"] is True


def test_validate_no_header_blocks():
    r = validate_format({"flat_text": "juste du texte sans avis ni reference", "_page_debut": 2})
    assert r["action"] == "bloquer" and r["alerte_humaine"] is True


def test_validate_multipage_range():
    r = validate_format({"flat_text": CLEAN, "page_debut": 3, "page_fin": 5})
    assert r["pages"] == "3-5"
    assert any("plusieurs pages" in x for x in r["raisons"])
