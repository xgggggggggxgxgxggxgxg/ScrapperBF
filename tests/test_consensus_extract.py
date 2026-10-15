"""Tests hors-ligne de l'extraction par consensus (providers injectés)."""
from consensus_extract import consensus_extract, field_divergence, _merge
from layer3_extract_llm import load_field_schema

FIELDS = load_field_schema("AVIS")

A = {"autorite_emettrice": "COMMUNE DE X", "objet": "Acquisition de PC",
     "type_avis": "Avis de demande de prix", "numero_reference": "N° 2026-1"}
B = {"autorite_emettrice": "COMMUNE DE X", "objet": "Acquisition d'ordinateurs",
     "type_avis": "Avis de demande de prix", "numero_reference": "N° 2026-1"}


def _prov(name, payload):
    def fn(prompt, schema, system_prompt, strict):
        return dict(payload)
    return {"name": name, "fn": fn}


def test_field_divergence():
    d, champs = field_divergence(A, B, FIELDS)
    assert "objet" in champs                 # objet diffère
    assert 0 < d < 1


def test_field_divergence_identical():
    d, champs = field_divergence(A, A, FIELDS)
    assert d == 0.0 and champs == []


def test_merge_prefers_non_empty():
    m = _merge({"objet": ""}, {"objet": "Acquisition"}, FIELDS)
    assert m["objet"] == "Acquisition"


def test_consensus_three_models_arbitrates():
    arb = {"champs": {**A, "objet": "Acquisition de PC"},
           "desaccords": [{"champ": "objet", "a": "pc", "b": "ordinateurs", "choisi": "pc"}]}
    provs = [_prov("mA", A), _prov("mB", B), _prov("arbitre", arb)]
    out = consensus_extract("texte annonce", "AVIS", providers=provs)
    assert out["objet"] == "Acquisition de PC"          # valeur de l'arbitre
    c = out["_consensus"]
    assert c["n_modeles"] == 3 and c["arbitre"] == "arbitre"
    assert "objet" in c["champs_en_desaccord"]


def test_consensus_two_models_merges():
    provs = [_prov("mA", A), _prov("mB", B)]
    out = consensus_extract("texte", "AVIS", providers=provs)
    assert out["_consensus"]["n_modeles"] == 2
    assert out["_consensus"]["arbitre"] is None


def test_consensus_high_divergence_alerts():
    far = {"autorite_emettrice": "AUTRE MINISTERE", "objet": "Travaux de route",
           "type_avis": "Appel d'offres", "numero_reference": "N° 9999"}
    provs = [_prov("mA", A), _prov("mB", far)]
    out = consensus_extract("texte", "AVIS", providers=provs, seuil_divergence=0.3)
    assert out["_consensus"]["alerte_humaine"] is True


def test_consensus_no_provider_returns_none():
    assert consensus_extract("texte", "AVIS", providers=[]) is None
