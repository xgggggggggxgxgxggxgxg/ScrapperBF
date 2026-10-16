"""Tests hors-ligne du split AVIS / RÉSULTATS (logique pure de partition)."""
from section_split import (
    _section_ranges, _label_page, _type_head_in,
    parse_sommaire, sommaire_section_pages, sommaire_ranges, _to_index, _ranges_sane,
)


SOMMAIRE = """SOMMAIRE
RESULTATS PROVISOIRES DES MINISTERES
Travaux ................................ P. 5
Fournitures et Services courants ....... P. 12
RESULTATS PROVISOIRES DES REGIONS
Travaux ................................ P. 30
AVIS DE MARCHES DES MINISTERES
Travaux ................................ P. 45
Fournitures et Services courants ....... P. 52
AVIS DE MARCHES DES REGIONS
Prestations intellectuelles ............ P. 80
"""


def test_parse_sommaire_tracks_context_and_pages():
    entries = parse_sommaire(SOMMAIRE)
    pages_res = [e["page"] for e in entries if e["context"] == "RESULTATS"]
    pages_avis = [e["page"] for e in entries if e["context"] == "AVIS"]
    assert pages_res == [5, 12, 30]
    assert pages_avis == [45, 52, 80]


def test_parse_sommaire_ignores_bp_false_positive():
    # « B.P. 621 » ne doit pas être lu comme une référence de page
    entries = parse_sommaire("AVIS\nAdresse : 01 B.P. 621 Ouagadougou")
    assert entries == []


def test_sommaire_section_pages_spans():
    secs = sommaire_section_pages(parse_sommaire(SOMMAIRE))
    assert secs["RESULTATS"] == (5, 30)
    assert secs["AVIS"] == (45, 80)


def test_sommaire_ranges_resultats_then_avis():
    secs = {"RESULTATS": (5, 30), "AVIS": (45, 80)}
    p2i = {5: 4, 45: 44}                 # imprimé 5 -> index 4, imprimé 45 -> index 44
    rng = sommaire_ranges(secs, p2i, 94)
    assert rng["RESULTATS"] == (4, 43)
    assert rng["AVIS"] == (44, 93)


def test_to_index_extrapolates_from_nearest():
    assert _to_index(45, {5: 4}, 94) == 44      # 4 + (45-5)
    assert _to_index(5, {5: 4}, 94) == 4        # exact
    assert _to_index(10, {}, 94) == 9           # fallback imprimé-1


def test_ranges_sane_rejects_bad_bounds():
    assert _ranges_sane({"AVIS": (0, 5), "RESULTATS": (6, 10)}, 11) is True
    assert _ranges_sane({"AVIS": (5, 2)}, 11) is False      # start > end
    assert _ranges_sane({"AVIS": None, "RESULTATS": None}, 11) is False


def test_section_ranges_avis_then_resultats():
    # AVIS en premier (pages 0-2), RÉSULTATS ensuite (3-5), sommaire en 0 → N
    labels = ["N", "A", "A", "R", "R", "R"]
    r = _section_ranges(labels)
    assert r["AVIS"] == (0, 2)
    assert r["RESULTATS"] == (3, 5)


def test_section_ranges_resultats_then_avis():
    # RÉSULTATS d'abord (0-2), AVIS ensuite (3-5) — ordre inverse géré
    labels = ["R", "R", "R", "A", "A", "A"]
    r = _section_ranges(labels)
    assert r["RESULTATS"] == (0, 2)
    assert r["AVIS"] == (3, 5)


def test_section_ranges_stray_marker_is_absorbed_by_span():
    # une barre noire isolée en zone AVIS : la zone R reste l'intervalle contigu
    labels = ["A", "A", "A", "R", "R", "R", "R"]
    r = _section_ranges(labels)
    assert r["RESULTATS"] == (3, 6)
    assert r["AVIS"] == (0, 2)


def test_section_ranges_no_resultats_falls_back_to_all_avis():
    labels = ["N", "A", "A", "A"]
    r = _section_ranges(labels)
    assert r["AVIS"] == (0, 3)
    assert r["RESULTATS"] is None


def test_section_ranges_empty():
    r = _section_ranges([])
    assert r["AVIS"] is None and r["RESULTATS"] is None


def test_label_page_sommaire_is_neutral():
    # la page sommaire cite les deux sections mais ne doit PAS être classée
    assert _label_page("SOMMAIRE\nRÉSULTATS PROVISOIRES ... AVIS ...", True, True) == "N"


def test_label_page_resultats_header():
    assert _label_page("RÉSULTATS PROVISOIRES DES MINISTÈRES", True, False) == "R"


def test_label_page_avis_band():
    assert _label_page("MINISTERE ...\nAvis de demande de prix", False, True) == "A"


def test_label_page_mixed_prefers_black_bar():
    # page avec barre noire ET bandeau couleur → résultats (tableau structurant)
    assert _label_page("Avis de demande de prix", True, True) == "R"


def test_type_head_in_detects_header_line():
    assert _type_head_in("MINISTERE\nAvis d'appel d'offres ouvert\n...")
    assert not _type_head_in("texte sans en-tête de type")
