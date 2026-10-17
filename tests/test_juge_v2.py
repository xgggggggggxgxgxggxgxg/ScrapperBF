"""Tests hors-ligne de l'orchestrateur juge.py (scoreur + juge-LLM injecté)."""
import juge

GOOD = {
    "autorite_emettrice": "COMMUNE DE OUAGADOUGOU",
    "objet": "Acquisition de matériel informatique au profit de la commune",
    "type_avis": "Avis de demande de prix",
    "numero_reference": "N° 2026-001/CO",
    "montant_previsionnel_global": {"ttc": "80 000 000"},
    "date_limite_depot": "15/11/2026 à 09h00",
    "est_en_lots": False,
}
TEXT = ("Avis de demande de prix. Objet : acquisition de matériel informatique. "
        "Montant 80 000 000 FCFA. Garantie de soumission (1 000 000) FCFA. "
        "Offres avant le 15/11/2026 à 09h00. Lot unique. Le Président de la CAM.")
TEXT_GARBLED = "Lrtmnqs bcdfgh klmnpqrst vwxz tqrst zzz qsdfgh"


def _llm(champs, verdict, corrections=None):
    def fn(text, a, b):
        return {"champs": dict(champs), "verdict": verdict,
                "divergences": [], "corrections": corrections or [], "analyse": "test"}
    return fn


def test_vert_all_agree():
    dec = juge.juger_avis(TEXT, GOOD, GOOD, providers=["x"],
                          juge_llm_fn=_llm(GOOD, "bon"))
    assert dec["couleur"] == "vert" and dec["action"] == "base"
    assert dec["revue_humaine"] is False


def test_rouge_opposition_records_why():
    # juge-LLM dit "bon" mais sur des champs vides + texte corrompu -> scoreur "mauvais"
    dec = juge.juger_avis(TEXT_GARBLED, {"objet": "x"}, {"objet": "x"}, providers=["x"],
                          juge_llm_fn=_llm({"objet": "x"}, "bon"))
    assert dec["couleur"] == "rouge"
    assert dec["pourquoi_divergence_juges"]  # le POURQUOI est enregistré
    assert dec["verdict_llm"] == "bon" and dec["verdict_scoreur"] == "mauvais"


def test_rouge_both_mauvais_no_retry():
    dec = juge.juger_avis(TEXT_GARBLED, {"objet": "x"}, {"objet": "x"}, providers=["x"],
                          juge_llm_fn=_llm({"objet": "x"}, "mauvais"))
    assert dec["couleur"] == "rouge" and dec["action"] == "humain"


def test_rouge_critical_missing_triggers_retry():
    champs = dict(GOOD)
    champs.pop("montant_previsionnel_global")   # champ critique absent
    dec = juge.juger_avis(TEXT, champs, champs, providers=["x"],
                          juge_llm_fn=_llm(champs, "bon"), autoriser_reessai=True)
    assert dec["couleur"] == "rouge" and dec["action"] == "reessayer"
    assert "montant_previsionnel_global" in dec["regle_suggeree"]["champs"]
    # après réessai (plus de seconde chance) -> humain
    dec2 = juge.juger_avis(TEXT, champs, champs, providers=["x"],
                           juge_llm_fn=_llm(champs, "bon"), autoriser_reessai=False)
    assert dec2["action"] == "humain"


def test_jaune_non_critical_divergence():
    # A et B divergent sur type_avis (non critique) -> jaune
    b = dict(GOOD)
    b["type_avis"] = "Appel d'offres ouvert"
    dec = juge.juger_avis(TEXT, GOOD, b, providers=["x"], juge_llm_fn=_llm(GOOD, "bon"))
    assert dec["couleur"] == "jaune" and dec["action"] == "file_humaine"
    assert dec["revue_humaine"] is True


def test_scoreur_only_when_no_llm():
    # Pas de providers -> juge-LLM absent -> décision sur scoreur seul
    dec = juge.juger_avis(TEXT, GOOD, None, providers=[], juge_llm_fn=None)
    assert dec["verdict_llm"] is None
    assert dec["couleur"] in ("vert", "jaune", "rouge")


def test_providers_juge_prefers_distinct_model():
    # 3 providers -> le juge-LLM prend le 3e (distinct de A=0 et B=1)
    assert juge._providers_juge(["A", "B", "C"])[0] == "C"
    # 2 providers -> celui de B (distinct de A)
    assert juge._providers_juge(["A", "B"])[0] == "B"
    # 1 provider -> pas le choix
    assert juge._providers_juge(["A"]) == ["A"]
    assert juge._providers_juge([]) == []


def test_scoreur_independent_from_llm():
    # Brut A/B pauvre + texte corrompu, mais le juge-LLM "corrige" et dit bon.
    # Le scoreur note le BRUT -> mauvais -> opposition -> rouge (règle 3).
    dec = juge.juger_avis(TEXT_GARBLED, {"objet": "x"}, {"objet": "x"}, providers=["x"],
                          juge_llm_fn=_llm(GOOD, "bon"))
    assert dec["couleur"] == "rouge"
    assert dec["pourquoi_divergence_juges"]
    assert dec["score"] >= dec["score_brut"]   # final (sur GOOD) >= brut (sur champs pauvres)


def test_to_judge_compat_caps_confidence_by_color():
    vert = juge.to_judge_compat({"couleur": "vert", "score": 100, "revue_humaine": False,
                                 "champs_en_conflit": []})
    jaune = juge.to_judge_compat({"couleur": "jaune", "score": 100, "revue_humaine": True,
                                  "champs_en_conflit": []})
    rouge = juge.to_judge_compat({"couleur": "rouge", "score": 100, "revue_humaine": True,
                                  "champs_en_conflit": []})
    assert vert["confiance"] == 1.0 and vert["verdict"] == "valide"
    assert jaune["confiance"] == 0.75 and jaune["revue_humaine"] is True
    assert rouge["confiance"] == 0.5 and rouge["verdict"] == "rejete"


def test_traiter_avis_full_flow_with_retry():
    calls = {"n": 0}

    def extracteur(text, section, providers=None, hint="", **kw):
        calls["n"] += 1
        champs = dict(GOOD)
        if calls["n"] == 1:
            champs.pop("montant_previsionnel_global")  # 1re passe : critique manquant
        return {"a": champs, "b": champs, "section": section,
                "providers": ["A", "B"], "regles_appliquees": [], "degrade": False}

    # juge-LLM "écho" : il reflète l'extraction A (sinon il remplirait toujours).
    def llm_echo(text, a, b):
        return {"champs": dict(a), "verdict": "bon", "divergences": [],
                "corrections": [], "analyse": "echo"}

    dec = juge.traiter_avis(TEXT, "AVIS", numero="TEST", index=0, providers=["x", "y"],
                            extracteur=extracteur, juge_llm_fn=llm_echo,
                            ecrire_dossier=False)
    assert calls["n"] == 2                 # il y a bien eu 1 réessai
    assert dec.get("_reessaye") is True
    assert dec["couleur"] == "vert"        # le réessai a rempli le champ critique
