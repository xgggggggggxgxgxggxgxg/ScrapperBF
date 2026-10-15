from scrapperbf.feedback.store import FeedbackStore


def revue(unit_id="AVIS#1", verdict="A_VERIFIER", **kw):
    base = {
        "unit_id": unit_id,
        "kind": "DEMANDE_PRIX",
        "pages": [12],
        "verdict": verdict,
        "verdict_machine": "VALIDE",
        "confidence_machine": 0.91,
        "corrections": {"date_limite_depot": "14 aout 2026 a 09 heures 00"},
        "extracted": {"date_limite_depot": "14 aout 2026"},
        "alerts": [{"code": "CHAMP_SUSPECT", "judgement": "faux_positif"}],
    }
    base.update(kw)
    return base


def test_import_du_fichier_du_dashboard(store):
    counts = store.import_reviews({"document": "N4466", "reviews": [revue(), revue("AVIS#2")]})
    assert counts == {"reviews": 2, "corrections": 2, "alertes": 2}


def test_une_relecture_remplace_la_precedente_du_meme_relecteur(store):
    store.import_reviews({"document": "N4466", "reviews": [revue(verdict="VALIDE")]}, reviewer="ana")
    store.import_reviews({"document": "N4466", "reviews": [revue(verdict="REJETE")]}, reviewer="ana")
    assert store.stats()["reviews"] == 1
    assert store.agreement()["detail"] == {"VALIDE→REJETE": 1}


def test_deux_relecteurs_coexistent(store):
    store.import_reviews({"document": "N4466", "reviews": [revue()]}, reviewer="ana")
    store.import_reviews({"document": "N4466", "reviews": [revue()]}, reviewer="bob")
    assert store.stats()["reviews"] == 2


def test_accord_machine_humain(store):
    store.import_reviews({"document": "N1", "reviews": [
        revue("AVIS#1", verdict="VALIDE"), revue("AVIS#2", verdict="REJETE"),
    ]})
    assert store.agreement() == {
        "revues": 2, "accord": 0.5, "detail": {"VALIDE→VALIDE": 1, "VALIDE→REJETE": 1},
    }


def test_un_code_majoritairement_faux_positif_est_calme(store):
    for i in range(4):
        store.record_alert_feedback("N1", "CHAMP_SUSPECT", "faux_positif", f"AVIS#{i}")
    store.record_alert_feedback("N1", "CHAMP_SUSPECT", "confirme", "AVIS#9")
    assert "CHAMP_SUSPECT" in store.muted_alert_codes()


def test_un_code_peu_observe_n_est_pas_calme(store):
    store.record_alert_feedback("N1", "CHAMP_NON_ANCRE", "faux_positif")
    # Une seule observation ne fait pas une statistique.
    assert store.muted_alert_codes() == {}


def test_les_corrections_deviennent_des_consignes_de_prompt(store):
    store.import_reviews({"document": "N1", "reviews": [revue("AVIS#1"), revue("AVIS#2")]})
    prompt = store.prompt_supplement("DEMANDE_PRIX")
    assert "date_limite_depot" in prompt
    assert "14 aout 2026 a 09 heures 00" in prompt
    assert "extrait à tort" in prompt


def test_pas_de_consigne_sans_retours_suffisants(store):
    store.import_reviews({"document": "N1", "reviews": [revue("AVIS#1")]})
    # Une correction isolée ne justifie pas de réécrire le prompt.
    assert store.prompt_supplement("DEMANDE_PRIX") == ""


def test_les_consignes_sont_filtrees_par_nature(store):
    store.import_reviews({"document": "N1", "reviews": [revue("AVIS#1"), revue("AVIS#2")]})
    assert store.prompt_supplement("RESULTATS") == ""


def test_jugement_d_alerte_invalide_est_refuse(store):
    import pytest

    with pytest.raises(ValueError):
        store.record_alert_feedback("N1", "CODE", "peut-etre")


def test_format_de_liste_sans_document_est_accepte(store):
    counts = store.import_reviews([revue()])
    assert counts["reviews"] == 1


def test_une_revue_sans_identifiant_est_ignoree(store):
    assert store.import_reviews({"document": "N1", "reviews": [{"verdict": "VALIDE"}]})["reviews"] == 0
