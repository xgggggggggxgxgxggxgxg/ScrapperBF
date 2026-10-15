"""Tests hors-ligne du moteur de recherche par mots-clés."""
from search_avis import fold, tokenize, search, score_avis, simulate, DEMO_QUERIES

AVIS = [
    {"objet": "Acquisition de matériels informatiques au profit de la DGI",
     "autorite_emettrice": "MINISTERE DE L'ECONOMIE", "categorie": "Fournitures et Services courants",
     "_source_pdf": "Quotidien N°4500", "_page_debut": 40},
    {"objet": "Travaux de construction d'un CSPS à Ouagadougou",
     "autorite_emettrice": "COMMUNE DE OUAGADOUGOU", "categorie": "Travaux",
     "_source_pdf": "Quotidien N°4502", "_page_debut": 44},
    {"objet": "Sélection d'un consultant pour une étude de faisabilité",
     "autorite_emettrice": "REGION DES HAUTS-BASSINS", "categorie": "Prestations intellectuelles",
     "_source_pdf": "Quotidien N°4503", "_page_debut": 33},
]


def test_fold_removes_accents_and_case():
    assert fold("Prestations Intellectuelles à Ouagadougou") == "prestations intellectuelles a ouagadougou"
    assert fold("VÉHICULES") == "vehicules"
    assert fold(None) == ""


def test_tokenize_drops_stopwords():
    assert tokenize("travaux de construction à la commune") == ["travaux", "construction", "commune"]


def test_search_accent_insensitive():
    # requête sans accent trouve un objet avec accent, et inversement
    assert len(search(AVIS, "materiels")) == 1
    assert len(search(AVIS, "étude")) == 1


def test_search_and_mode_requires_all_tokens():
    assert len(search(AVIS, "construction ouagadougou")) == 1   # les deux présents
    assert len(search(AVIS, "construction informatiques")) == 0  # jamais ensemble


def test_search_or_mode():
    res = search(AVIS, "construction informatiques", mode="or")
    assert len(res) == 2  # l'un OU l'autre


def test_objet_weighted_above_other_fields():
    # "informatiques" est dans l'objet (poids 3) -> score élevé + bonus objet
    res = search(AVIS, "informatiques")
    assert res[0]["score"] >= 5.0
    assert "objet" in res[0]["champs"]


def test_ranking_orders_by_score():
    res = search(AVIS, "construction ouagadougou", mode="or")
    scores = [r["score"] for r in res]
    assert scores == sorted(scores, reverse=True)


def test_snippet_contains_keyword_context():
    res = search(AVIS, "construction")
    assert "construction" in res[0]["snippet"].lower()


def test_no_match_returns_empty():
    assert search(AVIS, "aéronautique spatiale") == []


def test_empty_query_returns_nothing():
    assert search(AVIS, "") == []
    assert search(AVIS, "de la des") == []  # que des mots vides


def test_score_zero_when_partial_and():
    s, fields, found = score_avis(AVIS[0], ["informatiques", "travaux"], mode="and")
    assert s == 0.0


def test_simulate_reports_counts_and_top():
    rep = simulate(AVIS, ["informatiques", "construction", "introuvable"])
    assert rep["informatiques"]["nb"] == 1
    assert rep["construction"]["nb"] == 1
    assert rep["introuvable"]["nb"] == 0
    assert rep["informatiques"]["top"][0]["bulletin"] == "Quotidien N°4500"


def test_demo_queries_are_defined():
    assert len(DEMO_QUERIES) >= 5
