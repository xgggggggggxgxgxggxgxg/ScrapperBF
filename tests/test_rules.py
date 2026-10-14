import pytest

from scrapperbf.qa.rules import (
    Status,
    check_field,
    check_grounded,
    is_placeholder,
    looks_garbled,
    parse_amount,
    parse_date,
)

SOURCE = (
    "Avis de demande de prix N°2026-012/MEF/SG/DMP. Le montant previsionnel est de "
    "25 000 000 F CFA TTC. Les offres sont recues le 14 aout 2026 a 09 heures 00. "
    "Delai d'execution 45 jours. Telephone 25 30 00 00."
)


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("1 500 000 F CFA", 1500000),
        ("1.500.000", 1500000),
        ("25 000 000 FCFA TTC", 25000000),
        ("12 345,00 F CFA", 12345),
        ("sans chiffre", None),
    ],
)
def test_lecture_des_montants(texte, attendu):
    assert parse_amount(texte) == attendu


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("14 aout 2026 a 09h00", (2026, 8, 14)),
        ("1er septembre 2026", (2026, 9, 1)),
        ("14/08/2026", (2026, 8, 14)),
        ("14-08-26", (2026, 8, 14)),
        ("le mois prochain", None),
    ],
)
def test_lecture_des_dates(texte, attendu):
    assert parse_date(texte) == attendu


@pytest.mark.parametrize("valeur", [None, "", "  ", "N/A", "neant", "non precise", "voir ci-dessus", [], {}, "-"])
def test_valeurs_de_remplissage_comptent_comme_absentes(valeur):
    assert is_placeholder(valeur)


def test_false_n_est_pas_une_valeur_absente():
    # `est_en_lots: false` est une information, pas un trou.
    assert not is_placeholder(False)
    assert check_field("est_en_lots", False).status is Status.OK


def test_montant_sans_milliers_est_suspect():
    assert check_field("montant_previsionnel", "25").status is Status.SUSPECT


def test_date_sans_heure_de_depot_est_suspecte():
    # L'heure est réglementaire : son absence trahit une ligne tronquée.
    assert check_field("date_limite_depot", "14 aout 2026", SOURCE).status is Status.SUSPECT
    assert check_field("date_limite_depot", "14 aout 2026 a 09 heures 00", SOURCE).ok


def test_reference_sans_chiffre_est_invalide():
    assert check_field("numero_reference", "DMP/SG").status is Status.INVALID


def test_valeur_absente_du_texte_source_est_signalee():
    # Cœur de la détection d'hallucination : des chiffres qui n'existent pas
    # dans la page ne peuvent pas avoir été lus sur la page.
    verdict = check_field("montant_previsionnel", "77 777 777 F CFA", SOURCE)
    assert verdict.status is Status.UNGROUNDED and "77777777" in verdict.evidence


def test_valeur_presente_dans_le_texte_est_ancree():
    assert check_field("montant_previsionnel", "25 000 000 F CFA", SOURCE).ok


def test_l_ancrage_ne_s_applique_pas_aux_champs_de_synthese():
    # Le LLM reformule légitimement un objet : exiger le littéral créerait du bruit.
    assert check_grounded("objet", "Acquisition de materiel informatique", SOURCE) is None


def test_l_ancrage_ignore_les_conteneurs():
    assert check_grounded("lots", [{"numero": 1}], SOURCE) is None


def test_telephone_hors_format_burkinabe():
    assert check_field("telephones", ["25 30 00 00"]).ok
    assert check_field("telephones", ["123"]).status is Status.SUSPECT


def test_nombre_de_plis_improbable():
    assert check_field("nombre_plis_recus", "3").ok
    assert check_field("nombre_plis_recus", "9000").status is Status.SUSPECT


def test_delai_sans_unite_est_suspect():
    assert check_field("delai_execution", "45 jours").ok
    assert check_field("delai_execution", "rapide").status is Status.SUSPECT


@pytest.mark.parametrize(
    "texte,abime",
    [
        ("Acquisition de materiel informatique", False),
        ("Acquiiiiisition |||| de @#$%^&*(){}[]<>", True),
        ("court", False),
    ],
)
def test_detection_de_texte_abime_par_l_ocr(texte, abime):
    assert looks_garbled(texte) is abime
