"""Tests hors-ligne de la structuration LLM des RÉSULTATS (providers injectés)."""
from resultats_llm import (
    RESULTATS_SCHEMA, build_resultats_prompt, extract_resultats_unit,
)

UNIT = {
    "emetteur": "MINISTERE DE L'ECONOMIE ET DES FINANCES",
    "macro_section": "MINISTERES",
    "page_debut": 3, "page_fin": 7,
    "nb_lots_detectes": 2,
    "lots": ["Lot 1 : acquisition de consommables informatique",
             "Lot 2 : acquisition de fournitures de bureau"],
    "flat_text": "ADS SARL -- -- 265 523 010 354 030 680 Conforme ...",
    "_numero": "N4503", "_source_pdf": "Quotidien N°4503",
}


def make_provider(response):
    def fn(prompt, schema, system_prompt, strict):
        return dict(response)
    return [{"name": "fake", "fn": fn}]


def test_schema_has_lots_and_montant_columns():
    props = RESULTATS_SCHEMA["properties"]
    assert "nombre_lots" in props and props["nombre_lots"]["type"] == "integer"
    soum = props["lots"]["items"]["properties"]["soumissionnaires"]["items"]["properties"]
    for col in ("montant_lu_htva", "montant_corrige_htva", "montant_lu_ttc", "montant_corrige_ttc"):
        assert col in soum
        assert set(soum[col]["properties"]) == {"min", "max"}


def test_prompt_contains_hints_and_flat_text():
    p = build_resultats_prompt(UNIT)
    assert "MINISTERE DE L'ECONOMIE" in p
    assert "Bandes « Lot » repérées automatiquement : 2" in p
    assert "Lot 1 : acquisition de consommables informatique" in p
    assert "ADS SARL" in p  # le texte aplati est inclus


def test_extract_adds_provenance_and_coherence():
    resp = {
        "autorite_contractante": "MINISTERE DE L'ECONOMIE ET DES FINANCES",
        "nombre_lots": 2,
        "lots": [
            {"numero": 1, "intitule": "Lot 1", "soumissionnaires": [{"nom": "ADS SARL"}]},
            {"numero": 2, "intitule": "Lot 2", "soumissionnaires": []},
        ],
    }
    out = extract_resultats_unit(UNIT, providers=make_provider(resp))
    assert out["_source_pdf"] == "Quotidien N°4503"
    assert out["page_debut"] == 3
    assert out["_coherence_lots"] is True  # 2 détectés == 2 structurés


def test_coherence_false_when_counts_mismatch():
    resp = {"autorite_contractante": "X", "nombre_lots": 1,
            "lots": [{"numero": 1, "soumissionnaires": []}]}
    unit = dict(UNIT, nb_lots_detectes=3)
    out = extract_resultats_unit(unit, providers=make_provider(resp))
    assert out["_coherence_lots"] is False  # 3 détectés != 1 structuré


def test_coherence_true_for_single_unnamed_lot():
    # 0 bande détectée mais 1 lot structuré (lot unique non étiqueté) = cohérent
    resp = {"autorite_contractante": "X", "nombre_lots": 1,
            "lots": [{"numero": 1, "soumissionnaires": []}]}
    unit = dict(UNIT, nb_lots_detectes=0, lots=[])
    out = extract_resultats_unit(unit, providers=make_provider(resp))
    assert out["_coherence_lots"] is True


def test_extract_returns_none_when_providers_fail(monkeypatch):
    import layer3_extract_llm as l3
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)

    def boom(prompt, schema, system_prompt, strict):
        raise l3.ProviderError("quota")

    out = extract_resultats_unit(UNIT, max_retries=1, providers=[{"name": "x", "fn": boom}])
    assert out is None
