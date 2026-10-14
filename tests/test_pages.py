from scrapperbf.core.pages import parse_pages


def test_numeros_de_page_imprimes_sont_conserves(bulletin):
    doc = parse_pages(bulletin)
    assert [p.page_number for p in doc.pages] == [1, 12, 13, 14, 16, 17]


def test_ordinal_et_numero_imprime_sont_distincts(bulletin):
    doc = parse_pages(bulletin)
    page = doc.page_by_number(16)
    # La page imprimée 16 est la 5e feuille : le saut de numérotation est un
    # signal QA, pas une erreur de parsing.
    assert page.ordinal == 5 and page.declared_number == 16


def test_blocs_portent_leur_page(bulletin):
    doc = parse_pages(bulletin)
    titres = [b for b in doc.blocks if b.type == "title" and "demande de prix N°" in b.text]
    assert titres and titres[0].page_number == 12


def test_tableau_est_lu_en_matrice(bulletin):
    doc = parse_pages(bulletin)
    tables = [b for b in doc.blocks if b.type == "table"]
    assert len(tables) == 1
    assert tables[0].matrix[0][0] == "Soumissionnaire"
    assert "ATTRIBUTAIRE" in tables[0].matrix[1][3]


def test_page_sommaire_est_identifiee(bulletin):
    doc = parse_pages(bulletin)
    assert doc.pages[0].is_toc is True
    assert all(not p.is_toc for p in doc.pages[1:])


def test_bandeau_et_url_sont_ecartes():
    md = "Contenu utile de l'annonce.\nwww.dgcmef.gov.bf\nN° 4466 - Vendredi 14 aout 2026\n**5**\n"
    doc = parse_pages(md)
    textes = [b.text for b in doc.pages[0].blocks]
    assert textes == ["Contenu utile de l'annonce."]


def test_entier_isole_en_tete_ne_coupe_pas_la_page():
    # Une énumération « 3 » en début de page ne doit pas être prise pour un
    # pied de page, sinon chaque liste numérotée fabriquerait des pages vides.
    doc = parse_pages("3\nPremiere ligne de contenu reel du document.\n**7**\n")
    assert len(doc.pages) == 1 and doc.pages[0].page_number == 7


def test_document_sans_numerotation_reste_exploitable():
    doc = parse_pages("Une annonce sans aucun pied de page numerote.")
    assert len(doc.pages) == 1 and doc.pages[0].page_number == 1
