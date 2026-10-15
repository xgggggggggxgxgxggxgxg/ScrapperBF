"""Rotation des fournisseurs LLM, sans aucun appel réseau."""

import pytest

from scrapperbf.extract.providers import (
    CallResult,
    Provider,
    ProviderError,
    _extract_json,
    ask,
    default_providers,
)


def faux_provider(name, env_key, reponse=None, erreur=None, journal=None):
    def call(system, user, schema, model):
        if journal is not None:
            journal.append(name)
        if erreur:
            raise erreur
        return reponse

    return Provider(name=name, env_key=env_key, call=call,
                    model_env=f"{name.upper()}_MODEL", default_model="modele-test")


@pytest.fixture
def cle(monkeypatch):
    def poser(*noms):
        for n in noms:
            monkeypatch.setenv(n, "cle-factice")
    return poser


@pytest.mark.parametrize(
    "brut",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Voici le résultat :\n{"a": 1}\nJ\'espère que cela convient.',
    ],
)
def test_le_json_est_extrait_malgre_le_bavardage(brut):
    # Les modèles gratuits encadrent souvent leur JSON malgré la consigne :
    # le refuser gaspillerait un appel qui a pourtant abouti.
    assert _extract_json(brut) == {"a": 1}


def test_une_reponse_sans_json_est_une_erreur():
    with pytest.raises(ProviderError):
        _extract_json("Je ne peux pas répondre à cette demande.")


def test_un_fournisseur_sans_cle_n_est_pas_tente(cle):
    journal = []
    cle("B_KEY")
    resultat = ask("s", "u", {}, [
        faux_provider("a", "A_KEY", reponse={"x": 1}, journal=journal),
        faux_provider("b", "B_KEY", reponse={"x": 2}, journal=journal),
    ])
    assert journal == ["b"] and resultat.data == {"x": 2}


def test_la_rotation_passe_au_suivant_apres_echec(cle):
    journal = []
    cle("A_KEY", "B_KEY")
    resultat = ask("s", "u", {}, [
        faux_provider("a", "A_KEY", erreur=ProviderError("[definitif] HTTP 401"), journal=journal),
        faux_provider("b", "B_KEY", reponse={"ok": True}, journal=journal),
    ], sleep=lambda s: None)
    assert resultat.provider == "b"
    assert journal == ["a", "b"]
    assert any("401" in e for e in resultat.errors)


def test_une_erreur_definitive_ne_declenche_pas_de_reessai(cle):
    journal = []
    cle("A_KEY")
    with pytest.raises(ProviderError):
        ask("s", "u", {}, [
            faux_provider("a", "A_KEY", erreur=ProviderError("[definitif] HTTP 401"), journal=journal),
        ], sleep=lambda s: None)
    # Une clé invalide ne se répare pas en réessayant.
    assert journal == ["a"]


def test_une_erreur_passagere_est_reessayee(cle):
    journal = []
    cle("A_KEY")
    with pytest.raises(ProviderError):
        ask("s", "u", {}, [
            faux_provider("a", "A_KEY", erreur=ProviderError("HTTP 429"), journal=journal),
        ], max_retries=2, sleep=lambda s: None)
    assert journal == ["a", "a", "a"]


def test_une_reponse_non_objet_est_refusee(cle):
    cle("A_KEY", "B_KEY")
    resultat = ask("s", "u", {}, [
        faux_provider("a", "A_KEY", reponse=["liste"]),
        faux_provider("b", "B_KEY", reponse={"ok": True}),
    ], sleep=lambda s: None)
    assert resultat.provider == "b"


def test_sans_aucune_cle_le_message_nomme_les_variables():
    with pytest.raises(ProviderError) as erreur:
        ask("s", "u", {}, default_providers())
    assert "GROQ_API_KEY" in str(erreur.value)


def test_un_palier_payant_sans_modele_choisi_est_saute(monkeypatch):
    monkeypatch.setenv("PAYANT_KEY", "cle")
    provider = Provider(name="payant", env_key="PAYANT_KEY",
                        call=lambda *a: {"ne": "doit pas être appelé"},
                        model_env="PAYANT_MODEL", default_model="")
    with pytest.raises(ProviderError) as erreur:
        ask("s", "u", {}, [provider], sleep=lambda s: None)
    # On ne devine pas un modèle facturé à la place de l'utilisateur.
    assert "aucun modèle défini" in str(erreur.value)


def test_les_fournisseurs_par_defaut_privilegient_le_gratuit():
    noms = [p.name for p in default_providers()]
    assert noms[0] == "groq"
    assert noms.index("openrouter-gratuit") < noms.index("openrouter-payant")


def test_le_resultat_trace_ce_qu_il_a_coute(cle):
    cle("A_KEY")
    resultat = ask("s", "u", {}, [faux_provider("a", "A_KEY", reponse={"ok": 1})])
    assert resultat.to_dict()["provider"] == "a"
    assert resultat.attempts == 1
