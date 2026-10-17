"""Tests hors-ligne de l'extraction A/B (providers injectés, sans réseau)."""
from extraction import extract_fields

A = {"autorite_emettrice": "COMMUNE DE X", "objet": "Acquisition de PC",
     "type_avis": "Avis de demande de prix"}
B = {"autorite_emettrice": "COMMUNE DE X", "objet": "Acquisition d'ordinateurs",
     "type_avis": "Avis de demande de prix"}


def _prov(name, payload):
    return {"name": name, "fn": lambda p, sc, sy, st: dict(payload)}


def test_two_providers_return_a_and_b():
    out = extract_fields("texte", "AVIS", providers=[_prov("A", A), _prov("B", B)])
    assert out["a"]["objet"] == "Acquisition de PC"
    assert out["b"]["objet"] == "Acquisition d'ordinateurs"
    assert out["degrade"] is False
    assert out["providers"] == ["A", "B"]


def test_single_provider_degraded():
    out = extract_fields("texte", "AVIS", providers=[_prov("A", A)])
    assert out["a"]["objet"] == "Acquisition de PC"
    assert out["b"] is None
    assert out["degrade"] is True


def test_no_provider_returns_none():
    assert extract_fields("texte", "AVIS", providers=[]) is None
