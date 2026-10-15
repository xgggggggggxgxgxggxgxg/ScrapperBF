"""Tests hors-ligne des fonctions pures du segmenteur RÉSULTATS (sans PDF)."""
from pdf_resultats_extract import (
    is_black, is_orange, detect_macro_section,
    _EMETTEUR_REGION_RE, MACRO_MINISTERES, MACRO_REGIONS,
)


def test_is_black_accepts_scalar_and_triplet():
    assert is_black(0.0) is True
    assert is_black((0, 0, 0)) is True
    assert is_black((0.05, 0.05, 0.05)) is True


def test_is_black_rejects_non_black():
    assert is_black(0.5) is False
    assert is_black((1, 1, 1)) is False
    assert is_black(None) is False


def test_is_orange_detects_lot_bands():
    assert is_orange((0.957, 0.69, 0.514)) is True
    assert is_orange((1.0, 0.851, 0.4)) is True


def test_is_orange_rejects_others():
    assert is_orange((0, 0, 0)) is False
    assert is_orange((1, 1, 1)) is False
    assert is_orange(0.8) is False
    assert is_orange(None) is False


def test_macro_detection_ministeres():
    t = "RESULTATS PROVISOIRES DES MINISTERES, INSTITUTIONS ET MAITRISES D'OUVRAGES DELEGUEES"
    assert detect_macro_section(t, None) == MACRO_MINISTERES


def test_macro_detection_regions():
    assert detect_macro_section("RESULTATS PROVISOIRES DES REGIONS", MACRO_MINISTERES) == MACRO_REGIONS


def test_macro_detection_ignores_sommaire_and_keeps_current():
    # La page Sommaire cite les deux sections : on ne doit pas basculer.
    t = "Sommaire\nRESULTATS PROVISOIRES DES MINISTERES ... DES REGIONS ..."
    assert detect_macro_section(t, MACRO_MINISTERES) == MACRO_MINISTERES


def test_macro_detection_no_header_keeps_current():
    assert detect_macro_section("Un texte quelconque sans en-tête", MACRO_REGIONS) == MACRO_REGIONS


def test_emetteur_region_regex():
    assert _EMETTEUR_REGION_RE.match("REGION DE BANKUI")
    assert _EMETTEUR_REGION_RE.match("COMMUNE DE OUAGADOUGOU")
    assert _EMETTEUR_REGION_RE.match("CONSEIL REGIONAL DU SAHEL")
    assert not _EMETTEUR_REGION_RE.match("MINISTERE DE L'ECONOMIE ET DES FINANCES")
    assert not _EMETTEUR_REGION_RE.match("SOCIETE NATIONALE DE GESTION")
