"""Extraction par motifs — cas pris dans les bulletins réels."""

from scrapperbf.extract.labels import (
    extract_all,
    extract_amounts,
    extract_bare_reference,
    extract_clauses,
    extract_contacts,
    extract_labels,
    extract_lots,
)

ENTETE = """Avis de demande de prix
n° : 2026-23/MSECU/SG/ONASER/DMP
Financement : Budget ONASER, exercice 2026
Identifiant de l'Autorité contractante : Office National de la Sécurite Routière
Montant prévisionnel : vingt un million cent quatre vingt six mille quatre cent quarante un
(21 186 441) FCFA HTVA, soit vingt cinq millions (25 000 000) FCFA TTC."""

CORPS = """1. La participation à la concurrence est ouverte à toutes les personnes physiques ou morales
agréées B2 B3 ou B4 du Ministère de la Construction pour autant qu'elles ne soient pas sous le
coup d'interdiction.
2. Le délai d'exécution est de : vingt un (21) jours pour chaque commande.
3. Les Candidats peuvent consulter le dossier à la Direction des Marchés Publics,
Téléphone : (00226) 25 37 44 78 , E-mail : onaser.bf@gmail.com, de 07 heures 30 à 16 heures.
4. Tout Candidat doit retirer un jeu complet moyennant paiement d'un montant non remboursable
de vingt cinq mille (25 000) FCFA à l'Agence Comptable.
5. Les offres accompagnées d'une garantie de soumission d'un montant de cinq cent mille
(500 000) FCFA devront parvenir ou être remises au secrétariat au plus tard le 12/10/2026 à
09 heures 00 TU.
6. L'ouverture des plis sera faite immédiatement en présence des Candidats.
7. Les soumissionnaires resteront engagés par leurs offres pour un délai de soixante (60) jours.
La visite des lieux est obligatoire."""


def test_les_etiquettes_de_l_entete_sont_lues():
    champs = extract_labels(ENTETE)
    assert champs["numero_reference"].value == "2026-23/MSECU/SG/ONASER/DMP"
    assert champs["financement"].value == "Budget ONASER, exercice 2026"
    assert champs["autorite_contractante"].value.startswith("Office National")
    # Une étiquette explicite est la source la plus sûre du corps.
    assert champs["financement"].confidence > 0.95


def test_une_etiquette_avec_alternation_capture_bien_sa_valeur():
    # Le motif de référence contient des alternatives : sans groupe non
    # capturant, la valeur n'existerait que dans la dernière branche.
    assert extract_labels("Référence : ABC-123")["numero_reference"].value == "ABC-123"


def test_une_mention_au_fil_de_la_phrase_n_est_pas_une_etiquette():
    # L'étiquette est ancrée en début de ligne : sinon toute phrase parlant de
    # financement serait prise pour une déclaration.
    assert "financement" not in extract_labels("Le présent financement : hors sujet ici")


def test_reference_posee_seule_sur_sa_ligne():
    texte = "Avis à manifestation d'intérêt\nN°2026-03/RGRK/PTUY/CKBIA\nDans le cadre du PPM."
    assert extract_bare_reference(texte)["numero_reference"].value == "N°2026-03/RGRK/PTUY/CKBIA"


def test_le_montant_est_recoupe_lettres_chiffres():
    champ = extract_amounts(ENTETE)["montant_previsionnel_global"]
    assert champ.value == {"htva": "21 186 441", "ttc": "25 000 000"}
    assert champ.cross_check is True
    assert champ.confidence > 0.98


def test_un_montant_discordant_voit_sa_confiance_chuter():
    texte = "Montant prévisionnel : vingt cinq millions (9 999) FCFA TTC"
    champ = extract_amounts(texte)["montant_previsionnel_global"]
    assert champ.cross_check is False and champ.confidence < 0.7


def test_les_clauses_du_corps_sont_lues():
    champs = extract_clauses(CORPS)
    assert "12/10/2026" in champs["date_limite_depot"].value
    assert "21" in champs["delai_execution"].value
    assert champs["garantie_soumission"].value.startswith("garantie de soumission")
    assert "25 000" in champs["acquisition_dossier"].value
    assert champs["visite_site"].value.startswith("La visite des lieux")
    # L'intitulé de l'avis est dans l'en-tête, pas dans les clauses.
    assert extract_clauses(ENTETE)["type_avis"].value.lower().startswith("avis de demande de prix")


def test_l_echeance_prime_sur_la_phrase_d_acheminement():
    # « au plus tard le … » est la valeur utile ; la longue phrase
    # d'acheminement n'est qu'un repli.
    assert extract_clauses(CORPS)["date_limite_depot"].value.startswith("12/10/2026")


def test_une_capture_sans_mot_est_refusee():
    # Reste de tableau capté par un motif : « réservée aux 1,00 4 ».
    assert "reservation" not in extract_clauses("La demande de prix est réservée aux 1,00 4")


def test_les_telephones_exigent_un_contexte_de_contact():
    champs = extract_contacts(CORPS)["renseignements_et_contact"].value
    assert champs["telephones"] == ["(00226) 25 37 44 78"]
    assert champs["courriels"] == ["onaser.bf@gmail.com"]


def test_un_numero_de_reference_n_est_pas_un_telephone():
    # « BMZ N° 2022 67 490 » contient huit chiffres groupés par deux.
    texte = "Financement : KfW Banque de développement - BMZ N° 2022 67 490"
    assert extract_contacts(texte) == {}


def test_les_telephones_sont_dedoublonnes():
    texte = "Téléphone : (00226) 25 37 44 78 et aussi Tél : 25 37 44 78"
    valeur = extract_contacts(texte)["renseignements_et_contact"].value
    assert len(valeur["telephones"]) == 1


def test_les_lots_sont_numerotes_et_ordonnes():
    texte = ("Lot 2 : Acquisition de fournitures de bureau au profit de la DGI\n"
             "Lot 1 : acquisition de consommables informatique au profit de la DGI\n"
             "Lot 1 : doublon à ignorer")
    lots = extract_lots(texte)
    assert [l["numero"] for l in lots] == [1, 2]
    assert lots[0]["designation"].startswith("acquisition de consommables")


def test_la_maquette_prime_sur_le_texte():
    resultat = extract_all(ENTETE + "\n" + CORPS, {"autorite_emettrice": "OFFICE NATIONAL",
                                                   "objet": "Régie médiatique"})
    assert resultat.fields["autorite_emettrice"].source == "maquette"
    assert resultat.fields["autorite_emettrice"].confidence == 1.0


def test_le_record_est_au_format_du_schema():
    resultat = extract_all(
        ENTETE + "\n" + CORPS
        + "\nLot 1 : acquisition de consommables informatiques"
        + "\nLot 2 : acquisition de fournitures de bureau"
    )
    record = resultat.as_record()
    assert record["est_en_lots"] is True and len(record["lots"]) == 2
    assert "financement" in record and "date_limite_depot" in record
