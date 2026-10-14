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
