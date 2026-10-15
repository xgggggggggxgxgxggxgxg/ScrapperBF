import pytest

from scrapperbf.core.titles import TitleKind, analyze_title, infer_marche_type


@pytest.mark.parametrize(
    "titre,attendu",
    [
        ("RESULTATS PROVISOIRES", TitleKind.RESULTATS),
        ("Synthese des resultats de la demande de prix", TitleKind.RESULTATS),
        ("Avis de demande de prix N°2026-012/MEF", TitleKind.DEMANDE_PRIX),
        ("AVIS D'APPEL D'OFFRES OUVERT ACCELERE", TitleKind.APPEL_OFFRES),
        ("AVIS A MANIFESTATION D'INTERET", TitleKind.MANIFESTATION_INTERET),
        ("DEMANDE DE PROPOSITIONS", TitleKind.DEMANDE_PROPOSITIONS),
        ("COMMUNIQUE RECTIFICATIF", TitleKind.RECTIFICATIF),
        ("AVIS D'ANNULATION DE L'APPEL D'OFFRES", TitleKind.ANNULATION),
        ("Acquisition de materiel informatique", TitleKind.OBJET),
        ("MINISTERE DE LA SANTE", TitleKind.AUTORITE),
    ],
)
def test_classement_des_natures(titre, attendu):
    assert analyze_title(titre).kind is attendu


def test_marqueur_de_section_nu_vs_objet_de_marche():
    # « Travaux » sépare deux annonces ; « Travaux de ... » EST une annonce.
    assert analyze_title("Travaux").kind is TitleKind.SECTION_MARKER
    assert analyze_title("Travaux de construction d'un batiment").kind is TitleKind.OBJET


def test_marqueurs_de_categorie():
    assert analyze_title("COLLECTIVITES TERRITORIALES").category == "COLLECTIVITES"
    assert analyze_title("MINISTERES ET INSTITUTIONS").category == "MINISTERES"
    assert analyze_title("MAITRISES D'OUVRAGES DELEGUEES").category == "MINISTERES"


def test_titre_inconnu_a_une_confiance_basse():
    a = analyze_title("Communique sans intitule reconnaissable")
    assert a.kind is TitleKind.UNKNOWN and a.confidence < 0.3


def test_les_preuves_sont_tracees():
    a = analyze_title("Avis de demande de prix a commandes")
    assert a.evidence and "demande de prix" in a.evidence[0]


def test_annulation_prime_sur_appel_offres():
    # Cumul de motifs : le titre contient « appel d'offres », mais l'annulation
    # est le motif le plus fort et décide de la nature.
    assert analyze_title("AVIS D'ANNULATION DE L'APPEL D'OFFRES N°2026-05").kind is TitleKind.ANNULATION


@pytest.mark.parametrize(
    "titre,type_attendu",
    [
        ("Travaux de rehabilitation d'une ecole", "Travaux"),
        ("Acquisition de vehicules", "Fournitures et Services courants"),
        ("Selection d'un consultant pour l'audit des comptes", "Prestations intellectuelles"),
        ("Communique divers", None),
    ],
)
def test_type_de_marche_deduit_de_l_objet(titre, type_attendu):
    assert infer_marche_type(titre) == type_attendu


def test_titre_vide_ne_fait_pas_planter():
    assert analyze_title("   ").kind is TitleKind.UNKNOWN
