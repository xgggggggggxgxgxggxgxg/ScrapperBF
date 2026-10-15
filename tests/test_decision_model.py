"""Tests hors-ligne du modèle de décision (route vers l'API ou non)."""
from decision_model import (
    decide, analyser_champ, montant_a_classer, hint_pour_llm, _fold, refine_if_needed,
    PB_VIDE_REQUIS, PB_VIDE_PRESENT, PB_A_CLASSER, PB_QUEUE_PARASITE, PB_TROP_LONG,
    PB_MONTANT_IMPLAUSIBLE, PB_TEXTE_CORROMPU,
)

TEXTE = ("Avis de demande de prix. Montant prévisionnel : (50 000 000) FCFA. "
         "Les offres devront parvenir avant le 19/10/2026 à 09 heure.")


def test_montant_a_classer():
    assert montant_a_classer({"brut": "x FCFA HTVA soit y TTC"}) is True
    assert montant_a_classer({"htva": "x", "ttc": "y"}) is False
    assert montant_a_classer("50M HTVA soit 60M TTC") is True
    assert montant_a_classer("50 000 000 FCFA") is False


def test_vide_mais_present_via_cue():
    # date_limite vide alors que le texte contient « avant le » -> à reprendre
    pb = analyser_champ("date_limite_depot", None, _fold(TEXTE))
    assert pb and pb["type"] == PB_VIDE_PRESENT


def test_vide_requis():
    pb = analyser_champ("objet", None, _fold("texte sans objet"))
    assert pb and pb["type"] == PB_VIDE_REQUIS


def test_vide_optionnel_sans_cue_pas_de_probleme():
    assert analyser_champ("numero_reference", None, _fold("texte neutre")) is None


def test_montant_a_classer_flagged():
    pb = analyser_champ("montant_previsionnel_global", {"brut": "50M HTVA soit 60M TTC"}, "")
    assert pb and pb["type"] == PB_A_CLASSER


def test_montant_classe_pas_de_probleme():
    assert analyser_champ("montant_previsionnel_global", {"htva": "(50 000 000) FCFA HTVA", "ttc": "(60 000 000) FCFA TTC"}, "") is None


def test_queue_parasite():
    pb = analyser_champ("financement", "Budget de l'État 2026 Cet", _fold("..."))
    assert pb and pb["type"] == PB_QUEUE_PARASITE


def test_trop_long():
    pb = analyser_champ("type_avis", "x" * 120, "")
    assert pb and pb["type"] == PB_TROP_LONG


def test_decide_aggregates_and_flags_needs_llm():
    extracted = {
        "autorite_emettrice": "MINISTERE X",
        "objet": "Acquisition de matériel",
        "montant_previsionnel_global": {"brut": "50M HTVA soit 60M TTC"},
        "date_limite_depot": None,
        "flat_text": TEXTE,
        "_page_debut": 24,
    }
    d = decide(extracted, TEXTE)
    assert d["needs_llm"] is True
    champs = set(d["champs_a_reprendre"])
    assert "montant_previsionnel_global" in champs
    assert "date_limite_depot" in champs
    # les clés méta (_page_debut) et flat_text ne sont pas examinées
    assert "_page_debut" not in champs and "flat_text" not in champs


def test_decide_clean_record_no_llm():
    extracted = {
        "autorite_emettrice": "MINISTERE X",
        "objet": "Acquisition de matériel",
        "montant_previsionnel_global": {"htva": "(50 000 000) FCFA HTVA", "ttc": "(60 000 000) FCFA TTC"},
        "date_limite_depot": "avant le 19/10/2026 à 09 heure",
        "financement": "Budget de l'État 2026",
    }
    d = decide(extracted, TEXTE)
    assert d["needs_llm"] is False
    assert d["problemes"] == []


def test_hint_mentions_fields():
    d = decide({"objet": None, "date_limite_depot": None}, "avant le 19/10")
    h = hint_pour_llm(d)
    assert "date_limite_depot" in h
    assert "HTVA" in h  # rappel de classement


def test_montant_implausible_flagged():
    d = decide({"autorite_emettrice": "X", "objet": "Y",
                "montant_previsionnel_global": {"ttc": "cinq cents (500) FCFA"}}, "texte")
    types = {p["type"] for p in d["problemes"]}
    assert PB_MONTANT_IMPLAUSIBLE in types


def test_corrupted_text_flagged():
    d = decide({"autorite_emettrice": "X", "objet": "Y"},
               "Lrtmnqs bcdfgh klmnpqrst vwxz tqrst zzz wxyz")
    types = {p["type"] for p in d["problemes"]}
    assert PB_TEXTE_CORROMPU in types


def test_refine_if_needed_skips_llm_when_clean():
    called = {"n": 0}

    def provider(prompt, schema, system_prompt, strict):
        called["n"] += 1
        return {"champs": {}, "analyse": []}

    clean = {
        "autorite_emettrice": "MINISTERE X", "objet": "Acquisition",
        "montant_previsionnel_global": {"htva": "(50 000 000) FCFA HTVA", "ttc": "(60 000 000) FCFA TTC"},
        "date_limite_depot": "avant le 19/10/2026 à 09 heure",
    }
    res, dec = refine_if_needed(clean, TEXTE, providers=[{"name": "p", "fn": provider}])
    assert dec["needs_llm"] is False
    assert called["n"] == 0        # l'API n'est PAS appelée sur un enregistrement propre
    assert res is clean


def test_refine_if_needed_calls_llm_when_problem():
    def provider(prompt, schema, system_prompt, strict):
        # l'API corrige : classe le montant et renseigne la date
        assert "reprendre" in prompt.lower()  # la consigne ciblée est bien passée
        return {"champs": {"montant_previsionnel_global": {"htva": "(50 000 000) FCFA HTVA", "ttc": "(60 000 000) FCFA TTC"},
                           "date_limite_depot": "avant le 19/10/2026 à 09 heure"},
                "analyse": []}

    dirty = {
        "autorite_emettrice": "MINISTERE X", "objet": "Acquisition",
        "montant_previsionnel_global": {"brut": "50M HTVA soit 60M TTC"},
        "date_limite_depot": None,
    }
    res, dec = refine_if_needed(dirty, TEXTE, providers=[{"name": "p", "fn": provider}])
    assert dec["needs_llm"] is True
    assert res["montant_previsionnel_global"] == {"htva": "(50 000 000) FCFA HTVA", "ttc": "(60 000 000) FCFA TTC"}
    assert res["date_limite_depot"] == "avant le 19/10/2026 à 09 heure"
