"""Lecture de la maquette : bandes colorées de couverture.

Les cas reproduits viennent des bulletins réels : bande dessinée deux fois
(fond + filet), jaune réutilisé pour les en-têtes de tableaux de la section
Résultats, objet composé sur deux lignes donc deux bandes.
"""

import pytest

from scrapperbf.core.layout import (
    Band,
    Role,
    bands_on_page,
    bands_to_markdown,
    extract_bands,
    group_notices,
)

ROUGE, JAUNE, CYAN, NOIR = (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 1.0), (0.0, 0.0, 0.0)


def fill(couleur, x0, y0, largeur, hauteur):
    return {"color": couleur, "rect": (x0, y0, x0 + largeur, y0 + hauteur)}


def mot(texte, x0, y0, largeur=80, hauteur=9):
    return (x0, y0, x0 + largeur, y0 + hauteur, texte, 0, 0, 0)


def page_avis():
    """Une page d'avis telle que composée dans le bulletin."""
    fills = [
        # La catégorie est centrée sur une demi-justification…
        fill(ROUGE, 180, 57, 248, 17),
        fill(ROUGE, 186, 57, 237, 17),   # …et dessinée deux fois (fond + filet)
        fill(JAUNE, 38, 83, 525, 13),
        fill(JAUNE, 42, 83, 516, 13),
        fill(CYAN, 38, 102, 527, 14),
        fill(CYAN, 42, 102, 516, 14),
    ]
    mots = [
        mot("Travaux", 200, 59, 60),
        mot("REGION", 60, 85, 60), mot("DU", 125, 85, 20), mot("GUIRIKO", 150, 85, 60),
        mot("Réalisation", 60, 104, 70), mot("hydraulique", 135, 104, 70),
    ]
    return fills, mots


def test_les_trois_bandes_sont_lues_dans_l_ordre():
    bands = bands_on_page(*page_avis(), page_number=27)
    assert [b.role for b in bands] == [Role.CATEGORIE, Role.EMETTEUR, Role.OBJET]
    assert bands[0].text == "Travaux"
    assert bands[1].text == "REGION DU GUIRIKO"
    assert bands[2].text == "Réalisation hydraulique"


def test_le_double_trace_ne_compte_qu_une_fois():
    # Chaque bande est dessinée deux fois : sans déduplication, chaque annonce
    # du bulletin serait comptée deux fois.
    bands = bands_on_page(*page_avis(), page_number=27)
    assert len(bands) == 3


def test_les_en_tetes_de_tableaux_jaunes_sont_ecartes():
    # Dans la section Résultats, le jaune habille aussi les en-têtes de
    # colonnes. Seule la largeur les distingue d'une bande d'annonce.
    fills = [fill(JAUNE, 40, 200, 99, 19), fill(JAUNE, 150, 200, 109, 19), fill(JAUNE, 270, 200, 104, 19)]
    mots = [mot("Soumissionnaires", 45, 203), mot("Montants", 155, 203), mot("Montant TTC", 275, 203)]
    assert bands_on_page(fills, mots, 14) == []


def test_le_bandeau_noir_des_resultats_est_reconnu():
    fills = [fill(NOIR, 38, 177, 536, 13)]
    mots = [mot("MINISTERE", 60, 179), mot("DES", 130, 179, 25), mot("FINANCES", 160, 179)]
    bands = bands_on_page(fills, mots, 3)
    assert [b.role for b in bands] == [Role.INSTITUTION]


def test_une_bande_sans_texte_est_ignoree():
    assert bands_on_page([fill(ROUGE, 180, 57, 248, 17)], [], 27) == []


def test_une_couleur_hors_maquette_est_ignoree():
    fills = [fill((0.65, 0.65, 0.65), 38, 100, 520, 13)]
    assert bands_on_page(fills, [mot("Prix de l'offre", 60, 102)], 8) == []


def test_une_bande_trop_haute_est_ignoree():
    # Un aplat de fond de page n'est pas une bande de titre.
    fills = [fill(JAUNE, 38, 100, 520, 400)]
    assert bands_on_page(fills, [mot("Information", 60, 110)], 2) == []


# --- regroupement en annonces ----------------------------------------------

def bande(role, texte, page, y0=100.0):
    return Band(role, texte, page, y0, y0 + 13, 520.0, 13.0)


def test_chaque_bande_jaune_ouvre_une_annonce():
    notices = group_notices([
        bande(Role.CATEGORIE, "Travaux", 27, 57),
        bande(Role.EMETTEUR, "REGION DU GUIRIKO", 27, 83),
        bande(Role.OBJET, "Réalisation d'infrastructures", 27, 102),
        bande(Role.EMETTEUR, "COMMUNE DE BEKUY", 28, 83),
        bande(Role.OBJET, "Travaux de réalisation d'une maternité", 28, 102),
    ])
    assert [n.emetteur for n in notices] == ["REGION DU GUIRIKO", "COMMUNE DE BEKUY"]


def test_la_categorie_se_propage_jusqu_a_la_suivante():
    notices = group_notices([
        bande(Role.CATEGORIE, "Travaux", 27, 57),
        bande(Role.EMETTEUR, "REGION DU GUIRIKO", 27, 83),
        bande(Role.EMETTEUR, "COMMUNE DE BEKUY", 28, 83),
        bande(Role.CATEGORIE, "Prestations intellectuelles", 33, 57),
        bande(Role.EMETTEUR, "COMMUNE DE KOUMBIA", 33, 83),
    ])
    assert [n.categorie for n in notices] == ["Travaux", "Travaux", "Prestations intellectuelles"]


def test_un_objet_sur_deux_lignes_est_recolle():
    notices = group_notices([
        bande(Role.EMETTEUR, "REGION DU GUIRIKO", 27, 83),
        bande(Role.OBJET, "Réalisation d'infrastructures dans la commune de Koloko,", 27, 102),
        bande(Role.OBJET, "Province du Kénédougou", 27, 116),
    ])
    assert notices[0].objet_text.endswith("Province du Kénédougou")


def test_un_objet_sans_emetteur_ne_cree_pas_d_annonce():
    assert group_notices([bande(Role.OBJET, "Orphelin", 27)]) == []


@pytest.mark.parametrize(
    "emetteur,portee",
    [
        ("OFFICE NATIONAL DE LA SECURITE ROUTIERE", "NATIONAL"),
        ("UNIVERSITE DANIEL OUEZZIN COULIBALY", "NATIONAL"),
        ("MINISTERE DE L'ECONOMIE ET DES FINANCES", "NATIONAL"),
        ("CENTRE HOSPITALIER REGIONAL DE KAYA", "NATIONAL"),
        ("REGION DU NAKAMBE", "REGIONAL"),
        ("COMMUNE DE OUAGADOUGOU", "REGIONAL"),
        # Un bureau privé mandaté comme maître d'ouvrage délégué n'est ni l'un
        # ni l'autre : le dire vaut mieux que de le ranger au hasard.
        ("FOCUS SAHEL DEVELOPMENT", "INDETERMINE"),
    ],
)
def test_portee_nationale_ou_regionale(emetteur, portee):
    assert group_notices([bande(Role.EMETTEUR, emetteur, 1)])[0].portee == portee


# --- rendu Markdown ---------------------------------------------------------

def test_le_role_de_couleur_est_balise_dans_le_markdown():
    md = bands_to_markdown([
        bande(Role.CATEGORIE, "Travaux", 27, 57),
        bande(Role.EMETTEUR, "REGION DU GUIRIKO", 27, 83),
    ])
    assert md[27] == ["# [CATEGORIE] Travaux", "## [EMETTEUR] REGION DU GUIRIKO"]


# --- extraction complète ----------------------------------------------------

class FauxPdf:
    def __init__(self, pages):
        self.pages = pages

    def page_count(self, path):
        return len(self.pages)

    def page_fills(self, path, index):
        return self.pages[index][0]

    def page_words(self, path, index):
        return self.pages[index][1]


def test_extraction_sur_plusieurs_pages():
    bands = extract_bands("b.pdf", FauxPdf([page_avis(), page_avis()]))
    assert len(bands) == 6
    assert {b.page for b in bands} == {1, 2}
    assert len(group_notices(bands)) == 2
