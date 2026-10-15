from scrapperbf.core.pages import parse_pages
from scrapperbf.qa.alerts import Code
from scrapperbf.qa.multipage import analyze_document, analyze_page_signals, group_pages


def test_les_signaux_de_bord_de_page_sont_lus(bulletin):
    doc = parse_pages(bulletin)
    sig = {s.page_number: s for s in [analyze_page_signals(p) for p in doc.pages]}
    assert sig[12].starts_notice is True
    assert sig[12].ends_with_signature is True
    # La page 13 est coupée en pleine phrase ; la 14 porte la signature du maire.
    assert sig[13].ends_truncated is True and sig[13].ends_with_signature is False
    assert sig[14].ends_with_signature is True


def test_les_pages_d_une_meme_annonce_sont_chainees(bulletin):
    _, spans, _ = analyze_document(parse_pages(bulletin))
    multipage = [s for s in spans if s.is_multipage]
    assert any(s.pages == [13, 14] for s in multipage)


def test_la_numerotation_reglementaire_sert_de_preuve(bulletin):
    _, spans, _ = analyze_document(parse_pages(bulletin))
    span = next(s for s in spans if s.pages == [13, 14])
    assert any("numérotation continue" in r for r in span.reasons)
    assert span.confidence >= 0.9


def test_une_annonce_multipage_est_signalee(bulletin):
    _, _, alerts = analyze_document(parse_pages(bulletin))
    codes = [a.code for a in alerts]
    assert Code.ANNONCE_MULTIPAGE in codes


def test_la_numerotation_qui_repart_coupe_l_annonce():
    md = (
        "## Avis de demande de prix\n1. Premier point de l annonce initiale avec du contenu.\n"
        "2. Deuxieme point de l annonce initiale egalement.\n**20**\n"
        "1. Premier point d une annonce manifestement differente et nouvelle.\n"
        "2. Deuxieme point de cette autre annonce.\n**21**\n"
    )
    _, spans, _ = analyze_document(parse_pages(md))
    # La numérotation repart de 1 : c'est une rupture, pas une continuation.
    assert [s.pages for s in spans] == [[20], [21]]


def test_page_non_rattachable_leve_une_alerte():
    md = (
        "## Avis de demande de prix N°2026-01\nLe ministere lance une demande de prix.\n"
        "Le President de la Commission d attribution des marches publics.\n**30**\n"
        "Un contenu de page sans titre d annonce et sans lien avec la page precedente, "
        "qui se termine proprement par une phrase complete.\n**31**\n"
    )
    _, _, alerts = analyze_document(parse_pages(md))
    assert Code.PAGE_ORPHELINE in [a.code for a in alerts]


def test_le_sommaire_n_entre_pas_dans_le_chainage(bulletin):
    _, spans, _ = analyze_document(parse_pages(bulletin))
    assert all(1 not in s.pages for s in spans)


def test_doctrine_de_sous_fusion():
    # Sans aucun signal de continuation, on sépare : une fusion abusive
    # mélangerait deux annonces sans que rien ne le signale ensuite.
    from scrapperbf.qa.multipage import PageSignals

    a = PageSignals(1, 1, True, None, True, False, [1])
    b = PageSignals(2, 2, False, None, True, False, [1])
    assert [s.pages for s in group_pages([a, b])] == [[1], [2]]
