"""Tests hors-ligne du scoreur comportemental Span (scorer injecté)."""
import span_scorer as sp


def _scorer(mapping):
    """Doublure : renvoie les probabilités fournies (défaut 1.0)."""
    return lambda text, behaviors: {b: mapping.get(b, 1.0) for b in behaviors}


def test_score_behaviors_all_good():
    r = sp.score_behaviors("texte complet", scorer=_scorer({}))
    assert r["comportements_faibles"] == []
    assert r["revue_humaine"] is False


def test_score_behaviors_flags_critical_low():
    crit = "l'autorité émettrice est clairement identifiée"
    r = sp.score_behaviors("texte", scorer=_scorer({crit: 0.1}))
    assert crit in r["comportements_faibles"]
    assert r["revue_humaine"] is True           # comportement CRITIQUE improbable


def test_non_critical_low_does_not_force_review():
    noncrit = "le montant prévisionnel est présent (HTVA et/ou TTC)"
    r = sp.score_behaviors("texte", scorer=_scorer({noncrit: 0.2}))
    assert noncrit in r["comportements_faibles"]
    assert r["revue_humaine"] is False          # non critique -> signalé mais pas bloquant


def test_clamp_handles_bad_values():
    bad = lambda text, behaviors: {b: "pas un nombre" for b in behaviors}
    r = sp.score_behaviors("texte", scorer=bad)
    # valeurs illisibles -> 0.0 -> tous faibles -> revue (critiques présents)
    assert r["revue_humaine"] is True


def test_score_many_attaches_span_and_raises_review():
    crit = "le texte de l'annonce semble complet (non tronqué)"
    recs = [
        {"flat_text": "bon", "_judge": {"revue_humaine": False}},
        {"flat_text": "tronqué", "_judge": {"revue_humaine": False}},
    ]
    scorer = lambda text, behaviors: {b: (0.1 if (b == crit and text == "tronqué") else 1.0)
                                      for b in behaviors}
    sp.score_many(recs, scorer=scorer)
    assert recs[0]["_span"]["revue_humaine"] is False
    assert recs[0]["_judge"]["revue_humaine"] is False
    assert recs[1]["_span"]["revue_humaine"] is True
    assert recs[1]["_judge"]["revue_humaine"] is True   # remonté dans le juge


def test_score_many_skips_empty_text():
    recs = [{"_judge": {"revue_humaine": False}}]       # pas de texte
    sp.score_many(recs, scorer=_scorer({}))
    assert "_span" not in recs[0]


def test_enabled_false_without_key(monkeypatch):
    monkeypatch.delenv("SPAN_API_KEY", raising=False)
    monkeypatch.delenv("SPAN_API_KEYS", raising=False)
    monkeypatch.setenv("ENABLE_SPAN_SCORING", "1")
    assert sp.enabled() is False            # pas de clé -> inerte
