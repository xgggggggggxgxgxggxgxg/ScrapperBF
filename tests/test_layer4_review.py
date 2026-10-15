"""
Tests hors-ligne de la Couche 4 (auto-analyse + auto-complétion).

Aucun appel réseau : les providers LLM sont injectés via le paramètre
`providers` (liste de {name, fn}), fn ayant la signature unifiée
    fn(prompt, schema, system_prompt, strict) -> dict
"""
import layer4_review as l4
from layer4_review import (
    _is_empty,
    build_review_schema,
    build_review_prompt,
    merge_review,
    review_and_complete,
    load_field_schema,
    STATUT_ABSENT,
    STATUT_CORRIGE,
    STATUT_COMPLETE,
)


# --- Helpers ---------------------------------------------------------------

def make_provider(response: dict, name: str = "fake"):
    """Provider factice qui renvoie toujours `response`."""
    def fn(prompt, schema, system_prompt, strict):
        return dict(response)  # copie défensive
    return [{"name": name, "fn": fn}]


def make_failing_provider(exc: Exception, name: str = "boom"):
    def fn(prompt, schema, system_prompt, strict):
        raise exc
    return [{"name": name, "fn": fn}]


AVIS_FIELDS = load_field_schema("AVIS")


# --- _is_empty -------------------------------------------------------------

def test_is_empty_true_cases():
    for v in (None, "", "   ", [], {}, (), set()):
        assert _is_empty(v) is True


def test_is_empty_false_cases():
    # 0 et False sont des valeurs légitimes, pas des vides.
    for v in (0, False, "x", ["a"], {"k": 1}, "0"):
        assert _is_empty(v) is False


# --- Schéma & prompt -------------------------------------------------------

def test_build_review_schema_covers_all_fields():
    schema = build_review_schema(AVIS_FIELDS)
    champs_props = schema["properties"]["champs"]["properties"]
    for field in AVIS_FIELDS:
        assert field in champs_props
    assert schema["properties"]["analyse"]["type"] == "array"
    assert schema["required"] == ["champs", "analyse"]


def test_build_review_prompt_marks_empty_and_shows_values():
    extracted = {"autorite_emettrice": "Ministère X", "objet": None}
    prompt = build_review_prompt("TEXTE ANNONCE", extracted, AVIS_FIELDS)
    assert "TEXTE ANNONCE" in prompt
    assert "Ministère X" in prompt          # valeur existante reprise
    assert "objet: (VIDE" in prompt         # champ vide signalé
    # Tous les champs du schéma apparaissent pour une révision exhaustive.
    for field in AVIS_FIELDS:
        assert field in prompt


# --- merge_review ----------------------------------------------------------

def test_merge_review_corrects_completes_and_flags_absent():
    extracted = {
        "autorite_emettrice": "MAUVAISE VALEUR",
        "objet": None,
        "numero_reference": None,
        "_page_debut": 12,  # métadonnée à préserver
    }
    review_result = {
        "champs": {
            "autorite_emettrice": "Région du Centre",
            "objet": "Acquisition de véhicules",
            "numero_reference": None,
        },
        "analyse": [
            {"champ": "autorite_emettrice", "statut": STATUT_CORRIGE, "justification": "..."},
            {"champ": "objet", "statut": STATUT_COMPLETE, "justification": "..."},
            {"champ": "numero_reference", "statut": STATUT_ABSENT, "justification": "absent du texte"},
        ],
        "_provider": "fake",
    }
    completed = merge_review(extracted, review_result, AVIS_FIELDS)

    assert completed["autorite_emettrice"] == "Région du Centre"  # corrigé
    assert completed["objet"] == "Acquisition de véhicules"       # complété
    assert completed["numero_reference"] is None                  # resté null
    assert completed["_page_debut"] == 12                         # métadonnée préservée
    assert "numero_reference" in completed["_champs_absents"]
    # numero_reference est vide MAIS déclaré absent -> pas dans "à vérifier".
    assert "numero_reference" not in completed["_champs_a_verifier"]
    assert completed["_provider_review"] == "fake"


def test_merge_review_flags_empty_without_absent_as_a_verifier():
    extracted = {"objet": None}
    # Le LLM laisse 'objet' vide SANS le déclarer absent -> anomalie à relire.
    review_result = {"champs": {"objet": ""}, "analyse": []}
    completed = merge_review(extracted, review_result, AVIS_FIELDS)
    assert "objet" in completed["_champs_a_verifier"]
    assert "objet" not in completed["_champs_absents"]


def test_merge_review_preserves_original_field_if_not_returned():
    extracted = {"autorite_emettrice": "Ministère X"}
    review_result = {"champs": {}, "analyse": []}  # LLM n'a rien renvoyé
    completed = merge_review(extracted, review_result, AVIS_FIELDS)
    assert completed["autorite_emettrice"] == "Ministère X"


# --- review_and_complete (bout-en-bout, hors-ligne) ------------------------

def test_review_and_complete_offline_corrects_and_completes():
    extracted = {"autorite_emettrice": "FAUX", "objet": None}
    response = {
        "champs": {
            "autorite_emettrice": "Commune de Ouagadougou",
            "objet": "Travaux de voirie",
        },
        "analyse": [
            {"champ": "autorite_emettrice", "statut": STATUT_CORRIGE, "justification": "x"},
            {"champ": "objet", "statut": STATUT_COMPLETE, "justification": "y"},
        ],
    }
    completed = review_and_complete(
        "Texte de l'annonce", extracted, "AVIS",
        providers=make_provider(response),
    )
    assert completed is not None
    assert completed["autorite_emettrice"] == "Commune de Ouagadougou"
    assert completed["objet"] == "Travaux de voirie"
    assert completed["_provider_review"] == "fake"
    assert "_analyse" in completed


def test_core_guarantee_no_empty_unless_tracked():
    """Garantie demandée : après révision, tout champ vide est tracé
    (soit 'absent' réel, soit 'à vérifier'), jamais silencieusement perdu."""
    extracted = {name: None for name in AVIS_FIELDS}
    response = {
        "champs": {name: None for name in AVIS_FIELDS},
        "analyse": [
            {"champ": name, "statut": STATUT_ABSENT, "justification": "non présent"}
            for name in AVIS_FIELDS
        ],
    }
    completed = review_and_complete(
        "Texte minimal", extracted, "AVIS",
        providers=make_provider(response),
    )
    for name in AVIS_FIELDS:
        if _is_empty(completed.get(name)):
            assert (name in completed["_champs_absents"]
                    or name in completed["_champs_a_verifier"]), name


def test_review_and_complete_returns_none_when_all_providers_fail(monkeypatch):
    import layer3_extract_llm as l3
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)  # pas d'attente réelle
    out = review_and_complete(
        "Texte", {"objet": None}, "AVIS",
        max_retries=1,
        providers=make_failing_provider(l3.ProviderError("quota")),
    )
    assert out is None


def test_review_rejects_non_dict_response(monkeypatch):
    import layer3_extract_llm as l3
    monkeypatch.setattr(l3.time, "sleep", lambda *_: None)

    def fn(prompt, schema, system_prompt, strict):
        return ["pas", "un", "objet"]  # réponse malformée

    out = review_and_complete(
        "Texte", {"objet": None}, "AVIS",
        max_retries=1, providers=[{"name": "bad", "fn": fn}],
    )
    assert out is None
