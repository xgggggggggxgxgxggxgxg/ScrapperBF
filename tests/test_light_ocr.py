import pytest

from scrapperbf.ocr.light_ocr import extract_pdf, text_layer_confidence

PAGE_PROPRE = "Avis de demande de prix pour l'acquisition de materiel informatique. " * 40
PAGE_BRUITEE = "123 456 ||||| @#$% 789 000 ----- 111"


class FakePdf:
    """PDF de test : page 0 propre, page 1 sans texte, page 2 bruitée."""

    def __init__(self, pages=None):
        self.pages = pages if pages is not None else [PAGE_PROPRE, "", PAGE_BRUITEE]
        self.images_rendues = []

    def page_count(self, path):
        return len(self.pages)

    def page_text(self, path, index):
        return self.pages[index]

    def page_image(self, path, index, dpi):
        self.images_rendues.append((index, dpi))
        return f"image-{index}".encode()


class FakeOcr:
    def __init__(self, text="Texte reconnu par le moteur OCR sur la page scannee. " * 20, conf=0.82):
        self.text, self.conf = text, conf
        self.appels = 0

    def image_to_text(self, image, lang):
        self.appels += 1
        return self.text, self.conf


def test_une_page_propre_n_est_pas_ocrisee():
    pdf, ocr = FakePdf(), FakeOcr()
    report = extract_pdf("b.pdf", pdf, ocr)
    assert report.pages[0].source == "texte_integre"
    # Le coût de l'OCR n'est engagé que pour les pages qui en ont besoin.
    assert 0 not in [i for i, _ in pdf.images_rendues]


def test_une_page_sans_texte_est_ocrisee():
    report = extract_pdf("b.pdf", FakePdf(), FakeOcr())
    assert report.pages[1].source == "ocr"
    assert report.ocr_pages == [2, 3]


def test_l_ocr_est_ignore_s_il_n_apporte_rien():
    # Un OCR plus court que la couche de texte existante n'est pas un progrès.
    pdf = FakePdf([PAGE_BRUITEE * 20])
    report = extract_pdf("b.pdf", pdf, FakeOcr(text="court", conf=0.9))
    assert report.pages[0].source == "texte_integre"


def test_absence_de_moteur_ocr_ne_fait_pas_echouer():
    report = extract_pdf("b.pdf", FakePdf(), None)
    assert [p.source for p in report.pages] == ["texte_integre", "vide", "texte_integre"]
    assert "aucun moteur OCR installé" in report.pages[1].note


def test_une_page_illisible_n_interrompt_pas_le_document():
    class PdfCasse(FakePdf):
        def page_text(self, path, index):
            if index == 1:
                raise RuntimeError("flux xref corrompu")
            return self.pages[index]

    report = extract_pdf("b.pdf", PdfCasse(), FakeOcr())
    assert report.pages[1].source == "erreur" and len(report.pages) == 3


def test_un_echec_du_moteur_ocr_est_rapporte():
    class OcrCasse:
        def image_to_text(self, image, lang):
            raise RuntimeError("tesseract absent du PATH")

    report = extract_pdf("b.pdf", FakePdf(), OcrCasse())
    assert "OCR en échec" in report.pages[1].note


@pytest.mark.parametrize(
    "texte,plafond",
    [("", 0.0), ("trop court", 0.3), (PAGE_BRUITEE * 20, 0.5), (PAGE_PROPRE, 1.0)],
)
def test_jugement_de_la_couche_de_texte(texte, plafond):
    confidence, motif = text_layer_confidence(texte)
    assert confidence <= plafond and motif


def test_le_markdown_produit_est_relisible_par_le_parser():
    from scrapperbf.core.pages import parse_pages

    report = extract_pdf("b.pdf", FakePdf(), FakeOcr())
    doc = parse_pages(report.to_markdown())
    # Les pieds de page écrits par l'OCR sont ceux que le parser attend.
    assert [p.page_number for p in doc.pages] == [1, 2, 3]


def test_les_pages_faibles_sont_listees():
    report = extract_pdf("b.pdf", FakePdf(), FakeOcr(conf=0.4))
    assert report.weak_pages == [2, 3]
    assert report.confidence_by_page()[1] > 0.7


# --- promotion des titres d'après la mise en forme --------------------------

from scrapperbf.ocr.light_ocr import Line, body_text_size, heading_level, lines_to_markdown


def corps(texte, taille=9.0, gras=False):
    return Line(text=texte, size=taille, bold=gras)


CORPS_DE_PAGE = [corps(f"Ligne de corps de texte numero {i} de l'annonce.") for i in range(8)]


def test_la_taille_du_corps_est_la_mediane_ponderee():
    lignes = [*CORPS_DE_PAGE, corps("TITRE", 16.0)]
    # Un titre isolé ne doit pas déplacer la référence du corps de texte.
    assert body_text_size(lignes) == 9.0


def test_un_corps_plus_grand_devient_titre():
    assert heading_level(corps("AVIS DE DEMANDE DE PRIX", 14.0), 9.0) == 1
    assert heading_level(corps("Fournitures et Services courants", 10.5), 9.0) == 2
    assert heading_level(corps("Une ligne de corps de texte ordinaire.", 9.0), 9.0) == 0


def test_le_gras_sur_ligne_courte_sert_de_titre():
    assert heading_level(corps("MINISTERE DE LA SANTE", 9.0, gras=True), 9.0) == 2


def test_un_paragraphe_en_gras_n_est_pas_un_titre():
    # Les avis mettent des paragraphes entiers en gras : les promouvoir tous
    # noierait le classement sous de faux titres.
    long = corps("Les soumissionnaires resteront engages par leurs offres pendant " * 3, 9.0, gras=True)
    assert heading_level(long, 9.0) == 0


def test_le_rendu_markdown_promeut_les_titres():
    md = lines_to_markdown([
        corps("MINISTERE DE L'ECONOMIE", 15.0),
        corps("Avis de demande de prix N°2026-012", 11.0),
        *CORPS_DE_PAGE,
    ])
    assert md.startswith("# MINISTERE DE L'ECONOMIE")
    assert "## Avis de demande de prix N°2026-012" in md
    assert "Ligne de corps de texte numero 0" in md


def test_une_page_trop_courte_n_est_pas_devinee():
    md = lines_to_markdown([corps("DEUX LIGNES", 20.0), corps("seulement")])
    assert "#" not in md


class FakePdfMiseEnForme(FakePdf):
    """Backend qui expose la mise en forme, comme PyMuPDF."""

    def page_lines(self, path, index):
        return [
            Line("AVIS DE DEMANDE DE PRIX N°2026-012/MEF", 15.0, True),
            *[Line(f"{i}. Point numero {i} de l'annonce en cours de publication.", 9.0)
              for i in range(1, 9)],
        ]


def test_les_titres_promus_arrivent_dans_le_markdown():
    report = extract_pdf("b.pdf", FakePdfMiseEnForme(), FakeOcr())
    assert report.pages[0].text.startswith("# AVIS DE DEMANDE DE PRIX")


def test_les_titres_promus_sont_reclasses_par_la_plateforme():
    # Boucle complète : mise en forme du PDF → titre Markdown → nature d'annonce.
    from scrapperbf.core.pages import parse_pages
    from scrapperbf.core.segment import segment
    from scrapperbf.core.titles import TitleKind

    report = extract_pdf("b.pdf", FakePdfMiseEnForme(), FakeOcr())
    notices = segment(parse_pages(report.to_markdown()))
    assert any(n.kind is TitleKind.DEMANDE_PRIX for n in notices)


def test_un_backend_sans_mise_en_forme_le_signale():
    report = extract_pdf("b.pdf", FakePdf(), FakeOcr())
    assert "titres non promus" in report.pages[0].note


def test_un_echec_de_lecture_de_mise_en_forme_ne_perd_pas_la_page():
    class PdfMiseEnFormeCassee(FakePdf):
        def page_lines(self, path, index):
            raise RuntimeError("structure dict illisible")

    report = extract_pdf("b.pdf", PdfMiseEnFormeCassee(), FakeOcr())
    assert report.pages[0].source == "texte_integre" and report.pages[0].text


# --- pied de page : ne pas doubler le numéro imprimé ------------------------

def test_le_numero_imprime_n_est_pas_double():
    from scrapperbf.core.pages import parse_pages

    pdf = FakePdf([PAGE_PROPRE + "\n12\n", PAGE_PROPRE + "\n13\n"])
    report = extract_pdf("b.pdf", pdf, FakeOcr())
    doc = parse_pages(report.to_markdown())
    # Sans ce contrôle, le pied injecté s'ajoutait au pied imprimé et créait
    # une page vide à chaque frontière.
    assert [p.page_number for p in doc.pages] == [12, 13]


def test_le_numero_est_injecte_quand_la_page_n_en_a_pas():
    from scrapperbf.core.pages import parse_pages

    report = extract_pdf("b.pdf", FakePdf([PAGE_PROPRE, PAGE_PROPRE]), FakeOcr())
    doc = parse_pages(report.to_markdown())
    assert [p.page_number for p in doc.pages] == [1, 2]


def test_le_numero_de_page_n_est_jamais_promu_en_titre():
    # Le pied de page du bulletin est en gras et plus gros que le corps :
    # promu en titre, il cesserait d'être reconnu comme frontière de page.
    assert heading_level(corps("12", 14.0, gras=True), 9.0) == 0
    md = lines_to_markdown([corps("AVIS", 15.0), *CORPS_DE_PAGE, corps("12", 14.0, gras=True)])
    assert md.rstrip().endswith("\n12")
