"""OCR léger : ne reconnaître que ce qui doit l'être.

Les PDF du Quotidien sont hétérogènes : la plupart des pages embarquent déjà
une couche de texte (export numérique), quelques-unes sont des images scannées.
Passer tout le document à l'OCR coûte des minutes par bulletin et *dégrade* la
qualité des pages qui avaient déjà un texte propre.

La stratégie est donc page par page, du moins cher au plus cher :

1. lire la couche de texte intégrée (instantané, fidèle) ;
2. si cette couche est trop pauvre ou visiblement abîmée, et seulement alors,
   lancer l'OCR sur cette page à une résolution modérée.

Chaque page ressort avec sa provenance et une confiance, consommées ensuite par
le scoring QA : une annonce mal notée sur une page à faible confiance OCR est
un problème de numérisation, pas d'extraction, et le dashboard doit le dire.

Les dépendances (PyMuPDF, pytesseract) sont **optionnelles** : le module
s'importe et se teste sans elles, et les moteurs sont injectables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..core.text import normalize
from ..qa.rules import looks_garbled

# Seuils d'arbitrage. Une page A4 de bulletin fait plusieurs milliers de
# caractères : en dessous de MIN_CHARS, la couche de texte est inexploitable.
MIN_CHARS_PER_PAGE = 350
MIN_ALPHA_RATIO = 0.55
OCR_DPI = 200          # compromis lisibilité / vitesse pour du texte de presse
DEFAULT_LANG = "fra"


class PdfBackend(Protocol):
    """Accès minimal à un PDF, pour pouvoir injecter un faux en test."""

    def page_count(self, path: str) -> int: ...
    def page_text(self, path: str, index: int) -> str: ...
    def page_image(self, path: str, index: int, dpi: int) -> object: ...


class OcrBackend(Protocol):
    def image_to_text(self, image: object, lang: str) -> tuple[str, float]: ...


@dataclass
class PageText:
    """Texte d'une page et sa provenance."""

    index: int                 # 0-based, ordre physique dans le PDF
    text: str
    source: str                # "texte_integre" | "ocr" | "vide" | "erreur"
    confidence: float
    char_count: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "page": self.index + 1,
            "source": self.source,
            "confidence": round(self.confidence, 3),
            "char_count": self.char_count,
            "note": self.note,
        }


@dataclass
class OcrReport:
    """Bilan d'extraction d'un PDF."""

    path: str
    pages: list[PageText] = field(default_factory=list)

    @property
    def ocr_pages(self) -> list[int]:
        return [p.index + 1 for p in self.pages if p.source == "ocr"]

    @property
    def weak_pages(self) -> list[int]:
        return [p.index + 1 for p in self.pages if p.confidence < 0.7]

    @property
    def mean_confidence(self) -> float:
        return sum(p.confidence for p in self.pages) / len(self.pages) if self.pages else 0.0

    def confidence_by_page(self) -> dict[int, float]:
        return {p.index + 1: p.confidence for p in self.pages}

    def to_markdown(self) -> str:
        """Concatène les pages en Markdown avec le pied de page numéroté
        attendu par ``core.pages.parse_pages`` — l'OCR alimente ainsi le reste
        du pipeline sans format intermédiaire supplémentaire."""
        return "\n".join(f"{p.text.strip()}\n\n**{p.index + 1}**\n" for p in self.pages)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "page_count": len(self.pages),
            "mean_confidence": round(self.mean_confidence, 3),
            "ocr_pages": self.ocr_pages,
            "weak_pages": self.weak_pages,
            "pages": [p.to_dict() for p in self.pages],
        }


def text_layer_confidence(text: str) -> tuple[float, str]:
    """Juge la couche de texte intégrée d'une page.

    Retourne (confiance, motif). Trois symptômes trahissent une couche
    inutilisable : trop courte, trop peu de lettres (colonnes de chiffres mal
    découpées, bruit), ou motifs de répétition typiques d'une mauvaise
    reconnaissance antérieure.
    """
    stripped = text.strip()
    if not stripped:
        return 0.0, "aucun texte intégré"
    chars = len(stripped)
    alpha = sum(c.isalpha() or c.isspace() for c in stripped)
    alpha_ratio = alpha / chars

    if chars < MIN_CHARS_PER_PAGE:
        return 0.3, f"couche de texte très pauvre ({chars} caractères)"
    if alpha_ratio < MIN_ALPHA_RATIO:
        return 0.45, f"trop peu de lettres ({alpha_ratio:.0%}) : texte probablement bruité"
    if looks_garbled(stripped):
        return 0.5, "motifs de répétition typiques d'une reconnaissance dégradée"

    # Au-delà, la confiance croît avec la densité, plafonnée : une couche de
    # texte intégrée reste faillible (ligatures, colonnes entremêlées).
    return min(0.97, 0.7 + 0.27 * min(1.0, chars / 2500)), "couche de texte intégrée exploitable"


def _default_pdf_backend() -> PdfBackend | None:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore  # PyMuPDF < 1.24
        except ImportError:
            return None

    class _PyMuPdf:
        def page_count(self, path: str) -> int:
            with pymupdf.open(path) as doc:
                return doc.page_count

        def page_text(self, path: str, index: int) -> str:
            with pymupdf.open(path) as doc:
                return doc[index].get_text("text")

        def page_image(self, path: str, index: int, dpi: int):
            with pymupdf.open(path) as doc:
                return doc[index].get_pixmap(dpi=dpi).tobytes("png")

    return _PyMuPdf()


def _default_ocr_backend() -> OcrBackend | None:
    try:
        import io

        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return None

    class _Tesseract:
        def image_to_text(self, image: object, lang: str) -> tuple[str, float]:
            img = Image.open(io.BytesIO(image)) if isinstance(image, bytes) else image
            data = pytesseract.image_to_data(img, lang=lang, output_type=pytesseract.Output.DICT)
            words = [w for w in data.get("text", []) if w.strip()]
            confs = [float(c) for c in data.get("conf", []) if str(c) not in ("-1", "")]
            text = " ".join(words)
            # Tesseract rend des confiances en pourcentage par mot ; la moyenne
            # est grossière mais suffit à repérer une page illisible.
            mean = (sum(confs) / len(confs) / 100.0) if confs else 0.4
            return text, min(0.95, mean)

    return _Tesseract()


def extract_pdf(
    path: str | Path,
    pdf_backend: PdfBackend | None = None,
    ocr_backend: OcrBackend | None = None,
    lang: str = DEFAULT_LANG,
    dpi: int = OCR_DPI,
    force_ocr: bool = False,
    on_page: Callable[[PageText], None] | None = None,
) -> OcrReport:
    """Extrait le texte d'un PDF page par page, en n'OCRisant que le nécessaire.

    ``force_ocr`` court-circuite la couche de texte (utile pour comparer les
    deux lectures d'une page litigieuse).
    """
    path = str(path)
    pdf = pdf_backend or _default_pdf_backend()
    if pdf is None:
        raise RuntimeError(
            "Aucun moteur PDF disponible : installez PyMuPDF (pip install pymupdf) "
            "ou passez un backend explicite."
        )
    ocr = ocr_backend if ocr_backend is not None else _default_ocr_backend()

    report = OcrReport(path=path)
    for index in range(pdf.page_count(path)):
        page = _extract_one(pdf, ocr, path, index, lang, dpi, force_ocr)
        report.pages.append(page)
        if on_page:
            on_page(page)
    return report


def _extract_one(
    pdf: PdfBackend,
    ocr: OcrBackend | None,
    path: str,
    index: int,
    lang: str,
    dpi: int,
    force_ocr: bool,
) -> PageText:
    text, confidence, note = "", 0.0, ""
    if not force_ocr:
        try:
            text = pdf.page_text(path, index) or ""
        except Exception as exc:  # PDF partiellement corrompu : page isolée, pas tout le document
            return PageText(index, "", "erreur", 0.0, note=f"lecture impossible : {exc}")
        confidence, note = text_layer_confidence(text)

    needs_ocr = force_ocr or confidence < 0.6
    if needs_ocr:
        if ocr is None:
            return PageText(
                index, text, "texte_integre" if text.strip() else "vide", confidence,
                char_count=len(text),
                note=note + " — OCR souhaitable mais aucun moteur OCR installé "
                            "(pip install pytesseract pillow + tesseract-ocr-fra)",
            )
        try:
            image = pdf.page_image(path, index, dpi)
            ocr_text, ocr_conf = ocr.image_to_text(image, lang)
        except Exception as exc:
            return PageText(
                index, text, "texte_integre" if text.strip() else "erreur", confidence,
                char_count=len(text), note=f"{note} — OCR en échec : {exc}",
            )
        # On ne garde l'OCR que s'il apporte vraiment plus de matière : sur une
        # page mixte, un OCR bavard mais faux peut être plus long sans être meilleur.
        if len(normalize(ocr_text)) > len(normalize(text)) * 1.15 or not text.strip():
            return PageText(
                index, ocr_text, "ocr", ocr_conf, char_count=len(ocr_text),
                note=f"OCR appliqué ({note})",
            )
        return PageText(
            index, text, "texte_integre", max(confidence, 0.6), char_count=len(text),
            note=f"{note} — OCR tenté, sans gain",
        )

    return PageText(index, text, "texte_integre", confidence, char_count=len(text), note=note)
