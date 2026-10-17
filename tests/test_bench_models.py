"""Tests hors-ligne du banc d'essai (appelant injecté, aucune requête réseau)."""
from bench_models import (
    evaluate_models, evaluate_juges, evaluate_scoreurs, format_table, _match,
    ROLE_PRESETS, rank_models, build_report,
)


def test_rank_models_by_domain_metric():
    res = {
        "faible": {"confiance_juge_moyenne": 0.6, "remplissage_moyen": 0.5,
                   "erreurs": 1, "tokens_moyen_par_annonce": 900, "latence_moyenne_s": 1.0},
        "fort": {"confiance_juge_moyenne": 0.95, "remplissage_moyen": 0.9,
                 "erreurs": 0, "tokens_moyen_par_annonce": 1200, "latence_moyenne_s": 2.0},
    }
    assert rank_models("extracteur", res)[0] == "fort"     # meilleure confiance juge


def test_rank_tiebreak_prefers_fewer_errors_then_cheaper():
    res = {
        "cher": {"couverture_moyenne": 1.0, "erreurs": 0, "tokens_moyen_par_annonce": 500, "latence_moyenne_s": 1.0},
        "buggy": {"couverture_moyenne": 1.0, "erreurs": 2, "tokens_moyen_par_annonce": 100, "latence_moyenne_s": 0.5},
    }
    assert rank_models("scoreur", res)[0] == "cher"        # couverture égale -> moins d'erreurs


def test_build_report_has_sections_and_prod_line():
    rbr = {
        "extracteur": {"A:free": {"confiance_juge_moyenne": 0.95, "remplissage_moyen": 0.9,
                                  "json_valide": 5, "appels": 5, "taux_json_valide": 1.0,
                                  "tokens_moyen_par_annonce": 1200, "tokens_total": 6000,
                                  "latence_moyenne_s": 1.0, "debit_t_s": 100, "exactitude_gold": None, "erreurs": 0},
                       "B:free": {"confiance_juge_moyenne": 0.9, "remplissage_moyen": 0.85,
                                  "json_valide": 5, "appels": 5, "taux_json_valide": 1.0,
                                  "tokens_moyen_par_annonce": 1000, "tokens_total": 5000,
                                  "latence_moyenne_s": 1.0, "debit_t_s": 120, "exactitude_gold": None, "erreurs": 0}},
        "juge": {"ARB:free": {"explique_divergence": 0.9, "confiance_juge_moyenne": 0.9,
                              "json_valide": 5, "appels": 5, "taux_json_valide": 1.0,
                              "remplissage_moyen": 0.9, "tokens_moyen_par_annonce": 800,
                              "latence_moyenne_s": 2.0, "erreurs": 0}},
        "scoreur": {"SC:free": {"couverture_moyenne": 1.0, "json_valide": 5, "appels": 5,
                                "taux_json_valide": 1.0, "tokens_moyen_par_annonce": 300,
                                "latence_moyenne_s": 0.5, "erreurs": 0}},
    }
    md = build_report(rbr, {"pdf": "N4500.pdf", "n": 5, "date": "2026-10-17"})
    assert "# Rapport de banc d'essai" in md
    assert "Extracteurs" in md and "Arbitres" in md and "Scoreurs" in md
    assert "OPENROUTER_MODELS=A:free, B:free, ARB:free" in md   # 2 extracteurs + arbitre
    assert "SPAN_MODEL=SC:free" in md


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


def test_call_retry_retries_on_429(monkeypatch):
    import bench_models as bm
    monkeypatch.setattr(bm.time, "sleep", lambda *_: None)   # pas d'attente réelle
    monkeypatch.setattr(bm, "BENCH_RETRY_429", 3)
    n = {"c": 0}

    def flaky():
        n["c"] += 1
        if n["c"] < 3:
            raise RuntimeError("OpenRouter 429: temporarily rate-limited")
        return {"ok": True}
    res, dt, err = bm._call_retry(flaky)
    assert err is None and res == {"ok": True} and n["c"] == 3


def test_verbose_logs_progress(monkeypatch, caplog):
    import logging as _l, bench_models as bm
    monkeypatch.setattr(bm, "VERBOSE", True)
    with caplog.at_level(_l.INFO, logger="scrapperbf.bench"):
        evaluate_models([TEXT_OK], [{"name": "m/free", "kind": "x", "model": "m"}],
                        caller=_good, schema={})
    txt = caplog.text
    assert "extracteur m/free" in txt and "annonce 1/1" in txt and "✓" in txt


def test_call_retry_gives_up_on_non_429(monkeypatch):
    import bench_models as bm
    monkeypatch.setattr(bm.time, "sleep", lambda *_: None)
    n = {"c": 0}

    def auth_err():
        n["c"] += 1
        raise RuntimeError("OpenRouter 401: User not found")
    res, dt, err = bm._call_retry(auth_err)
    assert res is None and "401" in err and n["c"] == 1   # pas de retry sur une 401


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
