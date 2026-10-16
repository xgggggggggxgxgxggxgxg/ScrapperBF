"""Tests hors-ligne du banc d'essai (appelant injecté, aucune requête réseau)."""
from bench_models import evaluate_models, format_table, _match


TEXT_OK = ("MINISTERE X Acquisition de PC Avis de demande de prix n° 2026-1 "
           "Financement budget de l'État. Montant (60 000 000) TTC. devront parvenir "
           "avant le 19/10/2026 à 09 heure. Le Président de la Commission d'attribution")


def _good(spec, text, schema):
    res = {"autorite_emettrice": "MINISTERE X", "objet": "Acquisition de PC",
           "type_avis": "Avis de demande de prix", "numero_reference": "N° 2026-1",
           "financement": "budget de l'État", "date_limite_depot": "avant le 19/10/2026"}
    return res, {"prompt": 1000, "completion": 200, "total": 1200}, 1.5, None


def _broken_json(spec, text, schema):
    return None, None, 0.8, "JSON invalide"


def test_evaluate_counts_and_rates():
    res = evaluate_models([TEXT_OK, TEXT_OK], [{"name": "bon", "kind": "x", "model": "m"}],
                          caller=_good, schema={})
    s = res["bon"]
    assert s["appels"] == 2 and s["json_valide"] == 2 and s["erreurs"] == 0
    assert s["taux_json_valide"] == 1.0
    assert 0 < s["remplissage_moyen"] <= 1.0
    assert s["tokens_total"] == 2400
    assert s["tokens_moyen_par_annonce"] == 1200
    assert s["debit_t_s"] is not None


def test_evaluate_handles_errors():
    res = evaluate_models([TEXT_OK], [{"name": "ko", "kind": "x", "model": "m"}],
                          caller=_broken_json, schema={})
    s = res["ko"]
    assert s["json_valide"] == 0 and s["erreurs"] == 1
    assert s["taux_json_valide"] == 0.0
    assert s["remplissage_moyen"] is None


def test_gold_accuracy():
    gold = {"0": {"autorite_emettrice": "MINISTERE X", "objet": "ACHAT DIFFERENT"}}
    res = evaluate_models([TEXT_OK], [{"name": "bon", "kind": "x", "model": "m"}],
                          caller=_good, schema={}, gold=gold)
    # 1 champ correct (émetteur) sur 2 attendus -> 0.5
    assert res["bon"]["exactitude_gold"] == 0.5


def test_match_normalizes():
    assert _match("MINISTERE X", "ministere  x") is True
    assert _match("", "") is False
    assert _match("a", "b") is False


def test_format_table_contains_models():
    res = evaluate_models([TEXT_OK], [{"name": "google/gemma-4", "kind": "x", "model": "m"}],
                          caller=_good, schema={})
    t = format_table(res)
    assert "google/gemma-4" in t and "JSON%" in t
