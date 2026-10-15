"""Tests hors-ligne de la segmentation AVIS (plus d'extraction regex des champs)."""
from pdf_avis_extract import (
    enrich_coverage, CORE_FIELDS, TYPE_HEAD_RE, _numero_label,
    segment_avis_blocks, extract_avis_from_pdf, norm,
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
