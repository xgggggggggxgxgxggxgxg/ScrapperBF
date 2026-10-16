"""Tests hors-ligne du split AVIS / RÉSULTATS (logique pure de partition)."""
from section_split import _section_ranges, _label_page, _type_head_in


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
