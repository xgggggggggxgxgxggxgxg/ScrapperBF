"""Découpe des annonces en en-tête + corps, à partir de la maquette."""

import pytest

from scrapperbf.core.notices import Notice, clean_body_lines, extract_notices

ROUGE, JAUNE, CYAN = (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 1.0)


def fill(couleur, y0, largeur=520, hauteur=13, x0=38):
    return {"color": couleur, "rect": (x0, y0, x0 + largeur, y0 + hauteur)}


def mot(texte, x0, y0):
    return (x0, y0, x0 + 70, y0 + 9, texte, 0, 0, 0)


class FauxPdf:
    """Deux pages : une annonce qui s'ouvre, puis sa continuation."""

    def __init__(self, pages):
        self.pages = pages

    def page_count(self, path):
        return len(self.pages)

    def page_fills(self, path, index):
        return self.pages[index][0]

    def page_words(self, path, index):
        return self.pages[index][1]

    def page_text_lines(self, path, index):
        return self.pages[index][2]


PAGE_OUVERTURE = (
    [fill(ROUGE, 57, 248, 17, 180), fill(JAUNE, 83), fill(CYAN, 102, 520, 14)],
    [mot("Travaux", 200, 59), mot("REGION", 60, 85), mot("DU", 130, 85), mot("GUIRIKO", 160, 85),
     mot("Réalisation", 60, 104), mot("hydraulique", 140, 104)],
    [(59, "Travaux"), (85, "REGION DU GUIRIKO"), (104, "Réalisation hydraulique"),
     (160, "Avis de demande de prix n° : 2026-06/RGRK"),
     (175, "Financement : Budget communal, gestion 2026"),
     (190, "N° 4503 – Mardi 06 octobre 2026"),
     (205, "www.dgcmef.gov.bf  www.finances.gov.bf"),
     (220, "27")],
)
PAGE_CONTINUATION = (
    [],
    [],
    [(60, "Les offres devront parvenir au plus tard le 15/10/2026."),
     (80, "Le Président de la commission d'attribution des marchés.")],
)


def test_une_annonce_porte_son_entete_de_maquette():
    annonces = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE]), document="N4503")
    assert len(annonces) == 1
    a = annonces[0]
    assert a.emetteur == "REGION DU GUIRIKO"
    assert a.categorie_canonique == "Travaux"
    assert a.objet == "Réalisation hydraulique"


def test_le_corps_commence_sous_la_derniere_bande():
    a = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE]), document="N4503")[0]
    assert a.body_lines[0].startswith("Avis de demande de prix")
    # Le texte des bandes ne doit pas être repris dans le corps.
    assert "REGION DU GUIRIKO" not in a.body


def test_le_corps_se_poursuit_sur_la_page_suivante():
    # Le bulletin ne répète aucun en-tête quand une annonce déborde.
    a = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE, PAGE_CONTINUATION]), document="N4503")[0]
    assert a.page_debut == 1 and a.page_fin == 2
    assert "15/10/2026" in a.body


def test_une_nouvelle_bande_jaune_ferme_l_annonce_precedente():
    seconde = (
        [fill(JAUNE, 83), fill(CYAN, 102, 520, 14)],
        [mot("COMMUNE", 60, 85), mot("DE", 140, 85), mot("BEKUY", 170, 85),
         mot("Maternité", 60, 104)],
        [(85, "COMMUNE DE BEKUY"), (104, "Maternité"), (160, "Second corps d'annonce.")],
    )
    annonces = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE, seconde]), document="N4503")
    assert [a.emetteur for a in annonces] == ["REGION DU GUIRIKO", "COMMUNE DE BEKUY"]
    assert "Second corps" not in annonces[0].body


def test_les_bandeaux_sont_retires_du_corps():
    a = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE]), document="N4503")[0]
    # Sans ce nettoyage, la date de parution et le numéro du quotidien
    # deviennent des candidats sérieux pour « date limite » et « référence ».
    assert "www.dgcmef" not in a.body
    assert "Mardi 06 octobre 2026" not in a.body
    assert "\n27" not in a.body


def test_un_rectificatif_garde_la_reference_qu_il_cite():
    # Le bandeau n'est retiré qu'en fin de ligne : un rectificatif cite
    # légitimement « du Quotidien N°4502 – Lundi 05 octobre 2026, page 49 »,
    # et ce numéro identifie l'avis corrigé.
    lignes = clean_body_lines(
        ["Rectificatif du Quotidien N°4502 – Lundi 05 octobre 2026, page 49, portant sur l'avis"],
        set(),
    )
    assert lignes and "N°4502" in lignes[0]


def test_l_annonce_est_classee():
    a = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE]), document="N4503")[0]
    assert a.classification is not None
    assert a.classification.region == "Guiriko"
    assert a.portee == "REGIONAL"


def test_l_entete_donne_au_modele_ce_que_la_maquette_a_etabli():
    a = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE]), document="N4503")[0]
    assert a.header == {
        "autorite_emettrice": "REGION DU GUIRIKO",
        "objet": "Réalisation hydraulique",
        "categorie": "Travaux",
    }


def test_un_document_sans_bande_ne_donne_aucune_annonce():
    # Un supplément « (bis) » ne contient que des résultats.
    vide = ([], [], [(60, "SYNTHESE DES RESULTATS")])
    assert extract_notices("b.pdf", FauxPdf([vide]), document="N4503bis") == []


def test_le_dict_est_serialisable():
    d = extract_notices("b.pdf", FauxPdf([PAGE_OUVERTURE]), document="N4503")[0].to_dict()
    assert d["id"] == "N4503#1" and d["sector"] and d["pages"] == [1]
