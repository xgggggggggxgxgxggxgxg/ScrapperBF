"""Cascade d'extraction et modèle de décision.

Le modèle de décision est une fonction pure : on l'éprouve sans réseau, pour
savoir comment le système se comportera avant de l'exécuter.
"""

import pytest

from scrapperbf.core.priority import profile_for
from scrapperbf.core.titles import TitleKind
from scrapperbf.extract.cascade import (
    ESSENTIAL_THRESHOLD,
    Decision,
    Step,
    decide,
    essential_coverage,
    essential_fields,
    run_cascade,
)
from scrapperbf.qa.integrity import check_integrity

PROFIL = profile_for(TitleKind.DEMANDE_PRIX)

CORPS = """Avis de demande de prix
n° : 2026-23/MSECU/SG/ONASER/DMP
Financement : Budget ONASER, exercice 2026
Montant prévisionnel : vingt cinq millions (25 000 000) FCFA TTC.
1. Le délai d'exécution est de : vingt un (21) jours.
2. Consulter le dossier à la DMP, Téléphone : (00226) 25 37 44 78.
3. Retirer le dossier moyennant un montant non remboursable de vingt cinq mille
(25 000) FCFA.
4. Les offres devront parvenir au secrétariat au plus tard le 12/10/2026 à 09 heures 00 TU.
5. L'ouverture des plis sera faite immédiatement en présence des Candidats.
6. Les soumissionnaires resteront engagés pour un délai de soixante (60) jours.
Le Directeur Général."""

ENTETE_MAQUETTE = {
    "autorite_emettrice": "OFFICE NATIONAL DE LA SECURITE ROUTIERE",
    "objet": "Régie médiatique du TOUR DU FASO",
}


def page_saine():
    return check_integrity("u1", [21], CORPS, ends_with_signature=True)


def page_malsaine():
    return check_integrity("u1", [21], CORPS, notices_per_page={21: 2})


# --- périmètre des informations essentielles -------------------------------

def test_les_champs_optionnels_sortent_du_calcul():
    # Les compter ferait chuter la couverture d'une annonce pourtant complète.
    essentiels = essential_fields(PROFIL)
    assert "date_limite_depot" in essentiels and "signataire" not in essentiels


def test_la_couverture_est_ponderee_par_la_priorite():
    complet = {f: "valeur plausible" for f in essential_fields(PROFIL)}
    couverture, manquants = essential_coverage(complet, PROFIL)
    assert couverture == 1.0 and manquants == []

    sans_critique = dict(complet)
    del sans_critique["date_limite_depot"]
    partielle, manquants = essential_coverage(sans_critique, PROFIL)
    assert partielle < 1.0 and manquants == ["date_limite_depot"]


def test_une_valeur_de_remplissage_ne_compte_pas_comme_presente():
    couverture, manquants = essential_coverage({"objet": "néant", "financement": "N/A"}, PROFIL)
    assert couverture == 0.0 and "objet" in manquants


# --- modèle de décision -----------------------------------------------------

def test_complet_et_net_est_accepte():
    assert decide(0.95, {}).decision is Decision.ACCEPTER


def test_une_valeur_douteuse_declenche_la_relecture_ocr():
    # Une valeur présente mais aberrante vient plus souvent d'un texte mal
    # reconnu que d'un mauvais motif : on relit la page avant d'appeler le LLM.
    assert decide(0.95, {"montant": "suspect"}).decision is Decision.RELIRE_OCR


def test_un_doute_persistant_apres_ocr_passe_au_modele():
    assert decide(0.95, {"montant": "suspect"}, ocr_done=True).decision is Decision.COMPLETER_LLM


def test_une_couverture_insuffisante_appelle_le_modele():
    assert decide(0.60, {}).decision is Decision.COMPLETER_LLM


def test_sous_le_seuil_apres_le_modele_on_alerte():
    evaluation = decide(0.60, {}, llm_done=True)
    assert evaluation.decision is Decision.ALERTER
    assert f"{ESSENTIAL_THRESHOLD:.0%}" in " ".join(evaluation.reasons)


def test_une_page_non_fiable_alerte_malgre_des_valeurs_completes():
    # Un mélange de deux annonces produit des valeurs bien formées… attribuées
    # au mauvais marché. Aucun score ne peut le rattraper.
    evaluation = decide(0.95, {}, integrity=page_malsaine())
    assert evaluation.decision is Decision.ALERTER
    assert any("intégrité" in r for r in evaluation.reasons)


def test_sans_ocr_ni_modele_disponibles_on_alerte_directement():
    assert decide(0.60, {}, ocr_available=False, llm_available=False).decision is Decision.ALERTER


def test_le_seuil_exact_est_accepte():
    assert decide(ESSENTIAL_THRESHOLD, {}).decision is Decision.ACCEPTER


# --- exécution de la cascade ------------------------------------------------

def test_les_motifs_seuls_suffisent_sur_une_annonce_complete():
    resultat = run_cascade("u1", CORPS, PROFIL, header=ENTETE_MAQUETTE, integrity=page_saine())
    assert resultat.final.decision is Decision.ACCEPTER
    assert resultat.steps == [Step.INTEGRITE, Step.REGEX, Step.DECISION]
    # Aucun jeton dépensé : c'est tout l'intérêt de l'ordre des marches.
    assert not resultat.llm_called


def test_le_modele_n_est_interroge_que_sur_les_champs_manquants():
    demandes = {}

    def faux_llm(texte, champs):
        demandes["champs"] = champs
        return {"date_limite_depot": "12 octobre 2026 à 09 heures 00"}

    tronque = "Financement : Budget communal 2026\nObjet du marché quelconque."
    resultat = run_cascade("u1", tronque, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), ask_llm=faux_llm)
    assert "date_limite_depot" in demandes["champs"]
    # On ne redemande pas ce que les motifs ont déjà établi.
    assert "financement" not in demandes["champs"]
    assert resultat.fields["date_limite_depot"].source == "llm"


def test_le_modele_n_ecrase_pas_une_valeur_sure():
    def faux_llm(texte, champs):
        return {"financement": "valeur inventée", "date_limite_depot": "12/10/2026 à 09h00"}

    tronque = "Financement : Budget communal 2026\nObjet du marché quelconque."
    resultat = run_cascade("u1", tronque, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), ask_llm=faux_llm)
    assert resultat.fields["financement"].value == "Budget communal 2026"
    assert resultat.fields["financement"].source == "etiquette"


def test_la_relecture_ocr_est_tentee_avant_le_modele():
    appels = []

    def relire():
        appels.append("ocr")
        return CORPS   # la relecture lève le doute

    douteux = CORPS.replace("au plus tard le 12/10/2026 à 09 heures 00 TU", "au plus tard le 32/13/1899")
    resultat = run_cascade("u1", douteux, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), reread_ocr=relire)
    assert appels == ["ocr"]
    assert Step.OCR in resultat.steps


def test_un_echec_de_relecture_ocr_n_interrompt_pas_la_cascade():
    def relire():
        raise RuntimeError("tesseract absent")

    douteux = CORPS.replace("12/10/2026", "32/13/1899")
    resultat = run_cascade("u1", douteux, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), reread_ocr=relire)
    assert any("Relecture OCR impossible" in a.message for a in resultat.alerts)
    assert resultat.final is not None


def test_un_echec_du_modele_conserve_les_valeurs_des_motifs():
    def faux_llm(texte, champs):
        raise RuntimeError("quota dépassé")

    tronque = "Financement : Budget communal 2026\nObjet quelconque."
    resultat = run_cascade("u1", tronque, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), ask_llm=faux_llm)
    assert resultat.fields["financement"].value == "Budget communal 2026"
    assert any("Appel au modèle en échec" in a.message for a in resultat.alerts)


def test_l_alerte_administrateur_cite_le_seuil_et_les_manquants():
    resultat = run_cascade("u1", "Trois mots seulement.", PROFIL, integrity=page_saine())
    assert resultat.needs_admin
    alerte = resultat.alerts[-1]
    assert "informations essentielles" in alerte.message
    assert alerte.evidence  # les champs manquants sont nommés
    assert "dashboard" in alerte.hint


def test_les_alertes_d_integrite_sont_reprises_dans_le_resultat():
    resultat = run_cascade("u1", CORPS, PROFIL, header=ENTETE_MAQUETTE, integrity=page_malsaine())
    codes = [a.code for a in resultat.alerts]
    assert "SECTION_INCOHERENTE" in codes


def test_la_trace_des_marches_est_conservee():
    def relire():
        return CORPS

    def faux_llm(texte, champs):
        return {}

    resultat = run_cascade("u1", "Objet seul.", PROFIL, integrity=page_saine(),
                           reread_ocr=relire, ask_llm=faux_llm)
    # La trace permet de savoir ce qui a été dépensé, et pourquoi.
    assert resultat.steps.count(Step.DECISION) >= 2
    assert resultat.to_dict()["steps"][0] == Step.INTEGRITE.value


# --- branchement du juge ----------------------------------------------------

def test_le_score_du_juge_peut_refuser_une_extraction_complete():
    # La couverture dit la quantité, le juge dit la justesse : une extraction
    # complète mais peu fiable ne doit pas être acceptée.
    evaluation = decide(1.0, {}, reliability=0.40, ocr_available=False, llm_available=False)
    assert evaluation.decision is Decision.ALERTER
    assert any("fiabilité jugée" in r for r in evaluation.reasons)


def test_une_faible_fiabilite_tente_d_abord_l_ocr():
    assert decide(1.0, {}, reliability=0.40).decision is Decision.RELIRE_OCR


def test_une_faible_fiabilite_passe_ensuite_au_modele():
    assert decide(1.0, {}, reliability=0.40, ocr_done=True).decision is Decision.COMPLETER_LLM


def test_une_bonne_fiabilite_laisse_accepter():
    assert decide(0.95, {}, reliability=0.92).decision is Decision.ACCEPTER


def test_le_score_de_fiabilite_est_rapporte():
    resultat = run_cascade("u1", CORPS, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), rate=lambda record, texte: 0.88)
    assert resultat.reliability == 0.88
    assert resultat.to_dict()["reliability_percent"] == 88


def test_un_juge_en_echec_n_interrompt_pas_la_cascade():
    def juge_casse(record, texte):
        raise RuntimeError("modèle de notation indisponible")

    resultat = run_cascade("u1", CORPS, PROFIL, header=ENTETE_MAQUETTE,
                           integrity=page_saine(), rate=juge_casse)
    assert any("Notation de fiabilité impossible" in a.message for a in resultat.alerts)
    assert resultat.final is not None
