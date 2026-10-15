"""
Tests hors-ligne : score d'exactitude + boucle de rétroaction.
Aucun appel réseau ni fichier partagé (tmp_path pour le magasin).
"""
import layer4_review as l4
from layer4_review import (
    compute_exactitude,
    review_and_complete,
    load_field_schema,
    STATUT_CONFIRME,
    STATUT_CORRIGE,
    STATUT_ABSENT,
)
import feedback_store


AVIS_FIELDS = load_field_schema("AVIS")


def make_provider(response, name="fake"):
    def fn(prompt, schema, system_prompt, strict):
        return dict(response)
    return [{"name": name, "fn": fn}]


# --- compute_exactitude ----------------------------------------------------

def test_score_100_when_all_confirmed_full_confidence():
    completed = {name: f"v_{name}" for name in AVIS_FIELDS}
    completed["_analyse"] = [
        {"champ": name, "statut": STATUT_CONFIRME, "confiance": 1.0, "justification": "ok"}
        for name in AVIS_FIELDS
    ]
    completed["_champs_absents"] = []
    completed["_champs_a_verifier"] = []
    res = compute_exactitude(completed, AVIS_FIELDS)
    assert res["score"] == 100
    assert res["raisons"] == []


def test_optional_absent_does_not_lower_score():
    # numero_reference est optionnel : son absence légitime ne doit pas pénaliser.
    completed = {name: f"v_{name}" for name in AVIS_FIELDS}
    completed["numero_reference"] = None
    completed["_analyse"] = [
        {"champ": name, "statut": STATUT_CONFIRME, "confiance": 1.0}
        for name in AVIS_FIELDS if name != "numero_reference"
    ] + [{"champ": "numero_reference", "statut": STATUT_ABSENT, "justification": "absent"}]
    completed["_champs_absents"] = ["numero_reference"]
    completed["_champs_a_verifier"] = []
    res = compute_exactitude(completed, AVIS_FIELDS)
    assert res["score"] == 100


def test_required_absent_lowers_score_and_gives_reason():
    completed = {name: f"v_{name}" for name in AVIS_FIELDS}
    completed["objet"] = None  # 'objet' est required dans le schéma AVIS
    completed["_analyse"] = [
        {"champ": "objet", "statut": STATUT_ABSENT, "justification": "pas trouvé"}
    ]
    completed["_champs_absents"] = ["objet"]
    completed["_champs_a_verifier"] = []
    res = compute_exactitude(completed, AVIS_FIELDS)
    assert res["score"] < 100
    champs_en_raison = {r["champ"] for r in res["raisons"]}
    assert "objet" in champs_en_raison
    raison_objet = next(r for r in res["raisons"] if r["champ"] == "objet")
    assert "REQUIS" in raison_objet["raison"]


def test_low_confidence_field_generates_reason():
    completed = {name: f"v_{name}" for name in AVIS_FIELDS}
    completed["_analyse"] = [
        {"champ": name, "statut": STATUT_CONFIRME, "confiance": 1.0}
        for name in AVIS_FIELDS if name != "objet"
    ] + [{"champ": "objet", "statut": STATUT_CORRIGE, "confiance": 0.6,
          "justification": "deux formulations possibles"}]
    completed["_champs_absents"] = []
    completed["_champs_a_verifier"] = []
    res = compute_exactitude(completed, AVIS_FIELDS)
    assert res["score"] < 100
    raison = next(r for r in res["raisons"] if r["champ"] == "objet")
    assert "corrig" in raison["raison"].lower()
    assert raison["confiance"] == 0.6


def test_a_verifier_is_hard_zero():
    completed = {name: f"v_{name}" for name in AVIS_FIELDS}
    completed["objet"] = ""
    completed["_analyse"] = []
    completed["_champs_absents"] = []
    completed["_champs_a_verifier"] = ["objet"]
    res = compute_exactitude(completed, AVIS_FIELDS)
    assert res["score"] < 100
    raison = next(r for r in res["raisons"] if r["champ"] == "objet")
    assert raison["fiabilite"] == 0.0


# --- review_and_complete attache le score ----------------------------------

def test_review_attaches_score_and_reasons_when_not_perfect():
    extracted = {name: None for name in AVIS_FIELDS}
    response = {
        "champs": {name: None for name in AVIS_FIELDS},
        "analyse": [
            {"champ": name, "statut": STATUT_ABSENT, "justification": "absent"}
            for name in AVIS_FIELDS
        ],
    }
    completed = review_and_complete(
        "Texte", extracted, "AVIS", providers=make_provider(response)
    )
    # 'autorite_emettrice' et 'objet' sont required -> absents => score < 100.
    assert completed["_score_exactitude"] < 100
    assert completed["_raisons_ecart"]
    assert all("raison" in r for r in completed["_raisons_ecart"])


def test_review_feedback_hint_injected_into_prompt():
    seen = {}

    def fn(prompt, schema, system_prompt, strict):
        seen["prompt"] = prompt
        return {"champs": {}, "analyse": []}

    review_and_complete(
        "Texte", {"objet": "X"}, "AVIS",
        providers=[{"name": "spy", "fn": fn}],
        feedback_hint="- objet (problème rencontré 3 fois) : souvent tronqué",
    )
    assert "Retour d'expérience" in seen["prompt"]
    assert "souvent tronqué" in seen["prompt"]


# --- feedback_store (boucle) -----------------------------------------------

def test_feedback_record_then_hint_roundtrip(tmp_path):
    store = tmp_path / "fb.json"
    reasons = [
        {"champ": "date_limite_depot", "statut": "complete",
         "fiabilite": 0.6, "raison": "formulation inhabituelle 'devront parvenir'"},
    ]
    feedback_store.record(reasons, "N4466", "AVIS", score=92, path=store)
    hint = feedback_store.build_hint("AVIS", path=store)
    assert "date_limite_depot" in hint
    assert "devront parvenir" in hint
    # Un autre section n'hérite pas du retour.
    assert feedback_store.build_hint("RESULTATS", path=store) == ""


def test_feedback_hint_empty_when_no_history(tmp_path):
    assert feedback_store.build_hint("AVIS", path=tmp_path / "absent.json") == ""


def test_feedback_record_noop_on_empty_reasons(tmp_path):
    store = tmp_path / "fb.json"
    feedback_store.record([], "N1", "AVIS", path=store)
    assert not store.exists()  # rien enregistré


def test_feedback_hint_ranks_most_frequent_first(tmp_path):
    store = tmp_path / "fb.json"
    feedback_store.record(
        [{"champ": "objet", "raison": "r1"}], "N1", "AVIS", path=store)
    feedback_store.record(
        [{"champ": "objet", "raison": "r1"},
         {"champ": "financement", "raison": "r2"}], "N2", "AVIS", path=store)
    hint = feedback_store.build_hint("AVIS", path=store, max_items=1)
    assert "objet" in hint          # le plus fréquent (2x)
    assert "financement" not in hint  # tronqué par max_items


def test_summary_counts(tmp_path):
    store = tmp_path / "fb.json"
    feedback_store.record([{"champ": "objet", "raison": "r"}], "N1", "AVIS", path=store)
    feedback_store.record([{"champ": "objet", "raison": "r"}], "N2", "AVIS", path=store)
    s = feedback_store.summary(path=store)
    assert s["nb_annonces_en_ecart"] == 2
    assert tuple(s["champs_les_plus_fautifs"][0]) == ("objet", 2)
