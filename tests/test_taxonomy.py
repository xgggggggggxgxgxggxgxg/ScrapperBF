"""Classement par secteur, région et type d'émetteur.

Les cas viennent des bulletins réels : ce sont les objets et les émetteurs tels
qu'ils y sont écrits.
"""

import pytest

from scrapperbf.core.taxonomy import (
    EmitterType,
    Sector,
    classify,
    classify_emitter,
    classify_sector,
    detect_region,
)


def test_la_tete_de_l_objet_decide_du_secteur():
    # « Électrification » nomme ce qui est acheté ; « bâtiments » dit seulement
    # où. Sans prime à la tête, les deux secteurs étaient à égalité.
    secteur, _, preuves, secondaires = classify_sector("Électrification solaire de bâtiments publics")
    assert secteur is Sector.ENERGIE and "BTP" in secondaires
    assert any("tête" in p for p in preuves)


@pytest.mark.parametrize(
    "objet,secteur",
    [
        ("Acquisition de médicaments essentiels génériques", Sector.SANTE),
        ("Travaux de réalisation d'une maternité au CSPS de Bokuy", Sector.SANTE),
        ("Acquisition et installation de divers logiciels", Sector.INFORMATIQUE),
        ("Refonte du portail web de la Mairie de Ouagadougou", Sector.INFORMATIQUE),
        ("Réalisation et réhabilitation d'infrastructures hydrauliques", Sector.EAU_ASSAINISSEMENT),
        ("Travaux de réalisation d'un forage positif PMH", Sector.EAU_ASSAINISSEMENT),
        ("Travaux de construction d'un bâtiment administratif", Sector.BTP),
        ("Acquisition de motos au profit des VDP", Sector.TRANSPORT),
        ("Acquisition de tables-bancs au profit des écoles", Sector.EDUCATION),
        ("Régie médiatique du TOUR DU FASO", Sector.COMMUNICATION),
        ("Acquisition d'aliments pour bétail au profit des éleveurs", Sector.ELEVAGE),
        ("Acquisition de vivres céréaliers", Sector.ALIMENTATION),
        ("Électrification solaire de bâtiments publics", Sector.ENERGIE),
        ("Audit des comptes de l'exercice 2026", Sector.ETUDES_CONSEIL),
    ],
)
def test_classement_sectoriel(objet, secteur):
    assert classify_sector(objet)[0] is secteur


def test_le_secteur_vient_de_l_objet_pas_de_l_emetteur():
    # Un ministère de la Santé qui achète des ordinateurs fait un marché
    # informatique : le vendeur d'ordinateurs doit le trouver.
    c = classify("Acquisition d'ordinateurs portables", "MINISTERE DE LA SANTE")
    assert c.sector is Sector.INFORMATIQUE


def test_l_emetteur_sert_de_contexte_quand_l_objet_est_muet():
    # « palettes bois » ne dit pas le secteur ; la centrale d'achat de
    # médicaments le dit.
    c = classify("Acquisition de palettes bois rouge",
                 "CENTRALE D'ACHAT DES MEDICAMENTS ESSENTIELS GENERIQUES (CAMEG)")
    assert c.sector is Sector.SANTE
    # Mais avec moins d'assurance qu'un objet explicite.
    assert c.sector_confidence < 0.8


def test_un_second_secteur_est_conserve():
    # Construire une école relève du BTP autant que de l'éducation : le lecteur
    # doit la trouver sous les deux.
    c = classify("Travaux de construction de trois salles de classe à l'école de Bekuy")
    assert {c.sector.value, *c.secondary_sectors} >= {"BTP", "EDUCATION"}


def test_le_classement_rend_ses_preuves():
    c = classify("Acquisition de médicaments")
    # Les preuves citent le texte sous sa forme normalisée (sans accents).
    assert c.sector_evidence and "medicaments" in c.sector_evidence[0]


def test_un_objet_sans_terme_de_metier_reste_indetermine():
    secteur, confiance, preuves, _ = classify_sector("Acquisition de divers articles")
    assert secteur is Sector.AUTRE and confiance < 0.3 and preuves


def test_un_objet_absent_ne_fait_pas_planter():
    assert classify_sector("")[0] is Sector.AUTRE


# --- régions ----------------------------------------------------------------

@pytest.mark.parametrize(
    "texte,region",
    [
        ("REGION DU GUIRIKO", "Guiriko"),
        ("REGION DU NAKAMBE", "Nakambé"),
        ("Région de Oubri", "Oubri"),
        ("Province du Kénédougou, Région du Guiriko", "Guiriko"),
        ("REGION DE L'EST", "Est"),
        ("Région du Centre", "Centre"),
    ],
)
def test_detection_de_region(texte, region):
    assert detect_region(texte)[0] == region


def test_le_verbe_etre_n_est_pas_une_region():
    # « est » est le verbe être avant d'être une région : sans la tournure
    # « Région de l'Est », il classait 67 annonces du corpus en région Est.
    assert detect_region("Le montant prévisionnel est de 25 000 000 FCFA")[0] is None


def test_commune_et_province_sont_distinguees():
    region, commune, province = detect_region(
        "Réalisation dans la commune de Koloko, Province du Kénédougou, Région du Guiriko"
    )
    assert (region, commune, province) == ("Guiriko", "Koloko", "Kénédougou")


def test_un_nom_de_lieu_ne_deborde_pas_sur_la_phrase_suivante():
    # La capture traversait la frontière des champs : « Bekuy Travaux ».
    _, commune, _ = detect_region("COMMUNE DE BEKUY", "Travaux de réalisation d'une maternité")
    assert commune == "Bekuy"


# --- type d'émetteur --------------------------------------------------------

@pytest.mark.parametrize(
    "emetteur,type_",
    [
        ("MINISTERE DE LA SANTE", EmitterType.MINISTERE),
        ("COMMUNE DE OUAGADOUGOU", EmitterType.COLLECTIVITE),
        ("REGION DU NAKAMBE", EmitterType.COLLECTIVITE),
        ("CENTRE HOSPITALIER REGIONAL DE KAYA", EmitterType.SANTE_PUBLIQUE),
        ("UNIVERSITE DANIEL OUEZZIN COULIBALY", EmitterType.ENSEIGNEMENT),
        ("SOCIETE NATIONALE BURKINABE D'HYDROCARBURES (SONABHY)", EmitterType.SOCIETE_ETAT),
        ("PROJET D'APPUI A LA TRANSFORMATION DE L'AGRICULTURE", EmitterType.PROJET_PROGRAMME),
        ("AGENCE NATIONALE DES BASSINS HYDROGRAPHIQUES", EmitterType.ETABLISSEMENT_PUBLIC),
        ("OFFICE NATIONAL DE LA SECURITE ROUTIERE", EmitterType.ETABLISSEMENT_PUBLIC),
        ("FOCUS SAHEL DEVELOPMENT", EmitterType.AUTRE),
    ],
)
def test_type_d_emetteur(emetteur, type_):
    assert classify_emitter(emetteur) is type_


def test_le_classement_complet_est_serialisable():
    c = classify("Acquisition de médicaments", "CENTRE HOSPITALIER REGIONAL DE KAYA")
    d = c.to_dict()
    assert d["sector"] == "SANTE" and d["sector_label"] == "Santé"
    assert d["emitter_label"] == "Établissement de santé"
