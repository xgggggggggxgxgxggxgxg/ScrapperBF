"""Tests hors-ligne du banc d'essai (appelant injecté, aucune requête réseau)."""
from bench_models import (
    evaluate_models, evaluate_juges, evaluate_scoreurs, format_table, _match,
    ROLE_PRESETS,
)


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
    t = format_table(res, "extracteur")
    assert "google/gemma-4" in t and "JSON%" in t


def test_presets_cover_three_roles():
    assert len(ROLE_PRESETS["extracteur"]) == 5
    assert len(ROLE_PRESETS["juge"]) == 3
    assert len(ROLE_PRESETS["scoreur"]) == 2
    assert "respan/span-01-lite:free" in ROLE_PRESETS["scoreur"]


# --- rôle JUGE (arbitre) ----------------------------------------------------

def _arbiter(spec, text, a, b):
    # tranche en faveur de A, et explique s'il y a désaccord
    champs = dict(a)
    des = [{"champ": "x", "raison": "A plus fidèle"}] if a != b else []
    return {"champs": champs, "desaccords": des}, {"total": 900}, 2.0, None


def test_evaluate_juges_explains_divergence():
    texts = [TEXT_OK, TEXT_OK]
    A = {"autorite_emettrice": "MIN X", "objet": "Achat", "type_avis": "t",
         "numero_reference": "N1", "financement": "f", "date_limite_depot": "d"}
    B_diff = dict(A, objet="Achat DIFFERENT")
    ab = [(A, A), (A, B_diff)]        # 1er sans désaccord, 2e avec
    res = evaluate_juges(texts, [{"name": "arb", "kind": "x", "model": "m"}], ab, caller=_arbiter)
    s = res["arb"]
    assert s["json_valide"] == 2 and s["taux_json_valide"] == 1.0
    assert s["explique_divergence"] == 1.0     # le seul cas A≠B a bien un désaccord
    assert s["remplissage_moyen"] == 1.0


def test_evaluate_juges_handles_bad_output():
    bad = lambda spec, text, a, b: ({"pas_de_champs": 1}, None, 1.0, None)
    res = evaluate_juges([TEXT_OK], [{"name": "arb", "kind": "x", "model": "m"}],
                         [({}, {})], caller=bad)
    assert res["arb"]["json_valide"] == 0 and res["arb"]["erreurs"] == 1


# --- rôle SCOREUR -----------------------------------------------------------

def _span(spec, text, behaviors):
    return {b: 0.9 for b in behaviors}, {"total": 300}, 0.5, None


def test_evaluate_scoreurs_coverage():
    res = evaluate_scoreurs([TEXT_OK, TEXT_OK], [{"name": "span", "kind": "x", "model": "m"}],
                            caller=_span)
    s = res["span"]
    assert s["json_valide"] == 2
    assert s["couverture_moyenne"] == 1.0      # tous les comportements notés
    assert s["tokens_total"] == 600


def test_evaluate_scoreurs_partial_coverage():
    behaviors = ["b1", "b2", "b3", "b4"]
    part = lambda spec, text, bs: ({"b1": 0.8, "b2": "oops"}, {"total": 100}, 0.3, None)
    res = evaluate_scoreurs([TEXT_OK], [{"name": "span", "kind": "x", "model": "m"}],
                            behaviors=behaviors, caller=part)
    assert res["span"]["couverture_moyenne"] == 0.25   # 1 note valide sur 4
