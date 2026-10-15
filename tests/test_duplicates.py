from scrapperbf.core.pages import parse_pages
from scrapperbf.qa.alerts import Code, Severity
from scrapperbf.qa.duplicates import (
    check_pagination,
    duplicate_alerts,
    find_duplicate_pages,
    find_duplicate_units,
)


def test_aucun_doublon_sur_un_bulletin_sain(bulletin):
    assert find_duplicate_pages(parse_pages(bulletin)) == []


def test_page_rescannee_est_detectee(bulletin_doublon):
    groups = find_duplicate_pages(parse_pages(bulletin_doublon))
    assert groups and groups[0].kind == "exacte"
    assert groups[0].similarity > 0.98


def test_les_etiquettes_distinguent_deux_feuilles_de_meme_numero(bulletin_doublon):
    group = find_duplicate_pages(parse_pages(bulletin_doublon))[0]
    # Deux pages peuvent porter le même numéro imprimé : l'ordinal lève l'ambiguïté.
    assert group.members[0] != group.members[1]
    assert "feuille" in group.members[0]


def test_saut_et_repetition_de_numerotation(bulletin):
    kinds = {a.kind for a in check_pagination(parse_pages(bulletin))}
    # Le bulletin de test passe de la page 14 à la page 16.
    assert "saut" in kinds


def test_repetition_de_numero_detectee(bulletin_doublon):
    anomalies = check_pagination(parse_pages(bulletin_doublon))
    assert any(a.kind in ("repetition", "recul") for a in anomalies)


def test_annonces_quasi_identiques_detectees():
    texte = (
        "Le ministere lance une demande de prix pour l acquisition de materiel informatique "
        "au profit de la direction des marches publics pour un montant previsionnel de "
        "25 000 000 francs CFA avec remise des offres au plus tard le 14 aout 2026."
    )
    groups = find_duplicate_units([
        {"id": "AVIS#1", "flat_text": texte, "page_debut": 12},
        {"id": "AVIS#2", "flat_text": texte, "page_debut": 30},
        {"id": "AVIS#3", "flat_text": "Travaux de construction d un batiment " * 12, "page_debut": 40},
    ])
    assert len(groups) == 1
    assert set(groups[0].members) == {"AVIS#1", "AVIS#2"} and groups[0].pages == [12, 30]


def test_les_textes_trop_courts_ne_sont_pas_compares():
    # Sous le seuil de tokens, la similarité n'est pas fiable : ne rien dire
    # vaut mieux qu'une alerte infondée.
    assert find_duplicate_units([
        {"id": "A", "flat_text": "Avis court", "page_debut": 1},
        {"id": "B", "flat_text": "Avis court", "page_debut": 2},
    ]) == []


def test_une_page_dupliquee_produit_une_alerte_critique(bulletin_doublon):
    doc = parse_pages(bulletin_doublon)
    alerts = duplicate_alerts(find_duplicate_pages(doc), [], check_pagination(doc))
    page_alerts = [a for a in alerts if a.code == Code.PAGE_DUPLIQUEE]
    assert page_alerts and page_alerts[0].severity is Severity.CRITICAL
    assert page_alerts[0].hint and page_alerts[0].page_refs
