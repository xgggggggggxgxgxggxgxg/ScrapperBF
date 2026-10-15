"""Contrôle d'intégrité de la page, avant extraction."""

from scrapperbf.core.pages import parse_pages
from scrapperbf.qa.alerts import Code, Severity
from scrapperbf.qa.integrity import (
    check_integrity,
    notices_per_page,
    table_chars_in,
)

CORPS = "Le présent avis porte sur l'acquisition de matériel informatique. " * 8


def test_une_page_saine_autorise_l_extraction():
    rapport = check_integrity("u1", [21], CORPS)
    assert rapport.safe and rapport.problems == [] and rapport.alerts == []


def test_une_annonce_multipage_reste_exploitable():
    # Étalée sur plusieurs pages n'est pas un défaut en soi, dès lors que le
    # corps couvre bien toutes les pages : c'est signalé, pas bloquant.
    rapport = check_integrity("u1", [22, 23, 24], CORPS)
    assert rapport.safe
    assert rapport.spans_multiple_pages
    assert [a.code for a in rapport.alerts] == [Code.ANNONCE_MULTIPAGE]
    assert rapport.alerts[0].severity is Severity.INFO


def test_deux_annonces_sur_une_page_rendent_l_extraction_douteuse():
    rapport = check_integrity("u1", [21], CORPS, notices_per_page={21: 2})
    assert not rapport.safe
    assert Code.SECTION_INCOHERENTE in [a.code for a in rapport.alerts]


def test_un_corps_majoritairement_tabulaire_est_signale():
    rapport = check_integrity("u1", [21], CORPS, table_chars=len(CORPS) // 2)
    assert not rapport.safe
    assert any("tableaux" in p for p in rapport.problems)


def test_une_annonce_tronquee_est_signalee():
    rapport = check_integrity("u1", [21], CORPS, ends_with_signature=False)
    assert not rapport.safe
    assert Code.ANNONCE_TRONQUEE in [a.code for a in rapport.alerts]


def test_une_page_dupliquee_est_critique():
    rapport = check_integrity("u1", [21, 22], CORPS, duplicated_pages={22})
    alerte = next(a for a in rapport.alerts if a.code == Code.PAGE_DUPLIQUEE)
    assert alerte.severity is Severity.CRITICAL
    assert rapport.duplicated_pages == [22]


def test_un_corps_trop_court_est_signale():
    rapport = check_integrity("u1", [21], "Trois mots.")
    assert not rapport.safe
    assert Code.PAGE_VIDE in [a.code for a in rapport.alerts]


def test_toutes_les_alertes_portent_leurs_pages():
    rapport = check_integrity("u1", [21, 22], "court", notices_per_page={21: 3},
                              duplicated_pages={22}, ends_with_signature=False)
    assert rapport.alerts
    assert all(a.page_refs == [21, 22] and a.unit_id == "u1" for a in rapport.alerts)


def test_comptage_des_annonces_par_page():
    assert notices_per_page([21, 22, 22, 27]) == {21: 1, 22: 2, 27: 1}


def test_volume_de_tableau_sur_les_pages_d_une_annonce():
    md = ("Texte d'introduction de l'annonce.\n"
          "| Soumissionnaire | Montant |\n| ENTREPRISE A | 12 000 000 |\n**21**\n"
          "Autre page sans tableau du tout.\n**22**\n")
    doc = parse_pages(md)
    assert table_chars_in(doc, [21]) > 0
    assert table_chars_in(doc, [22]) == 0
