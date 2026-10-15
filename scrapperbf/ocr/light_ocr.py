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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..core.text import normalize
from ..qa.rules import looks_garbled

# --- promotion des titres ---------------------------------------------------
# Dans un PDF, un titre n'est pas balisé : il est seulement plus gros ou en
# gras que le corps du texte. Comme tout le tri des annonces s'appuie sur les
# titres, l'extraction doit les rendre visibles en Markdown, sinon la
# plateforme ne voit plus un seul titre sur un bulletin réel.
HEADING_SIZE_RATIO_H1 = 1.35   # au-delà : titre de premier niveau
HEADING_SIZE_RATIO_H2 = 1.12   # au-delà : titre de second niveau
HEADING_MAX_CHARS = 180        # un paragraphe en gros corps n'est pas un titre
MIN_LINES_FOR_BODY_SIZE = 5    # sous ce nombre, la taille du corps n'est pas fiable

# Seuils d'arbitrage. Une page A4 de bulletin fait plusieurs milliers de
# caractères : en dessous de MIN_CHARS, la couche de texte est inexploitable.
MIN_CHARS_PER_PAGE = 350
MIN_ALPHA_RATIO = 0.55
OCR_DPI = 200          # compromis lisibilité / vitesse pour du texte de presse
DEFAULT_LANG = "fra"


@dataclass
class Line:
    """Une ligne de texte avec ce qu'il faut pour juger si c'est un titre."""

    text: str
    size: float = 0.0
    bold: bool = False


class PdfBackend(Protocol):
    """Accès minimal à un PDF, pour pouvoir injecter un faux en test.

    ``page_lines`` est facultatif : un backend qui ne l'implémente pas donne du
    texte brut, et les titres ne sont alors plus détectables par leur mise en
    forme — l'extraction le signale au lieu de le taire.
    """

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
        du pipeline sans format intermédiaire supplémentaire.

        Le numéro n'est injecté que si la page ne porte pas déjà le sien. Le
        bulletin imprime son numéro en pied de page, et ce numéro-là est le bon
        (c'est celui que l'opérateur lit dans le PDF) : en ajouter un second
        fabriquerait une page vide à chaque frontière.
        """
        parts = []
        for page in self.pages:
            text = page.text.strip()
            parts.append(text if _ends_with_page_number(text) else f"{text}\n\n**{page.index + 1}**")
            parts.append("")
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "page_count": len(self.pages),
            "mean_confidence": round(self.mean_confidence, 3),
            "ocr_pages": self.ocr_pages,
            "weak_pages": self.weak_pages,
            "pages": [p.to_dict() for p in self.pages],
        }


# Un numéro de page imprimé tient seul sur sa ligne, en fin de page. On ne
# regarde que les toutes dernières lignes : un "12" isolé en milieu de page
# appartient à une énumération, pas au pied de page.
TRAILING_LINES_CHECKED = 3
PAGE_NUMBER_LINE_RE = re.compile(r"^\**\s*(\d{1,4})\s*\**$")


def _ends_with_page_number(text: str) -> bool:
    """La page porte-t-elle déjà son numéro imprimé en pied de page ?"""
    lignes = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return any(PAGE_NUMBER_LINE_RE.match(l) for l in lignes[-TRAILING_LINES_CHECKED:])


def body_text_size(lines: list[Line]) -> float:
    """Taille du corps de texte de la page : la médiane pondérée par longueur.

    Pondérer par le nombre de caractères évite qu'une page couverte de petits
    titres fasse passer le corps pour du titre, et inversement.
    """
    weighted: list[float] = []
    for line in lines:
        if line.size > 0 and line.text.strip():
            weighted.extend([line.size] * max(1, len(line.text.strip()) // 10))
    if not weighted:
        return 0.0
    weighted.sort()
    return weighted[len(weighted) // 2]


def heading_level(line: Line, body_size: float) -> int:
    """Niveau de titre d'une ligne, 0 si c'est du corps de texte."""
    text = line.text.strip()
    if not text or len(text) > HEADING_MAX_CHARS or not body_size:
        return 0
    if PAGE_NUMBER_LINE_RE.match(text):
        # Le numéro de page imprimé est en gras et plus gros que le corps : le
        # promouvoir en titre le rendrait invisible comme pied de page, et la
        # numérotation réelle du bulletin serait perdue.
        return 0
    ratio = line.size / body_size if line.size else 0.0
    if ratio >= HEADING_SIZE_RATIO_H1:
        return 1
    if ratio >= HEADING_SIZE_RATIO_H2:
        return 2
    # Le gras seul, à corps égal, sert aussi de titre dans le bulletin — mais
    # seulement sur une ligne courte, sinon un paragraphe entier mis en gras
    # (fréquent dans les avis) serait promu titre.
    if line.bold and ratio >= 0.98 and len(text) <= 120:
        return 2
    return 0


def lines_to_markdown(lines: list[Line]) -> str:
    """Rend les lignes en Markdown, titres promus selon leur mise en forme."""
    body_size = body_text_size(lines)
    if len([l for l in lines if l.text.strip()]) < MIN_LINES_FOR_BODY_SIZE:
        # Trop peu de lignes pour établir une référence : on ne devine pas.
        return "\n".join(l.text for l in lines)

    out: list[str] = []
    for line in lines:
        text = line.text.strip()
        if not text:
            out.append("")
            continue
        level = heading_level(line, body_size)
        out.append(f"{'#' * level} {text}" if level else text)
    return "\n".join(out)


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

        def page_lines(self, path: str, index: int) -> list[Line]:
            """Lignes avec leur corps et leur graisse, pour promouvoir les titres."""
            with pymupdf.open(path) as doc:
                data = doc[index].get_text("dict")
            lines: list[Line] = []
            for block in data.get("blocks", []):
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(s.get("text", "") for s in spans)
                    if not text.strip():
                        continue
                    # La taille retenue est celle du span le plus long : un
                    # exposant ou une puce ne doit pas décider du niveau.
                    main = max(spans, key=lambda s: len(s.get("text", "")), default={})
                    flags = main.get("flags", 0)
                    lines.append(Line(
                        text=text,
                        size=float(main.get("size", 0.0)),
                        bold=bool(flags & 16) or "bold" in str(main.get("font", "")).lower(),
                    ))
                lines.append(Line(text=""))  # frontière de bloc = ligne vide
            return lines

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
    layout_backend=None,
    read_layout: bool = True,
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
    if read_layout and layout_backend is None:
        from ..core.layout import default_backend as _layout_default

        layout_backend = _layout_default()

    report = OcrReport(path=path)
    for index in range(pdf.page_count(path)):
        page = _extract_one(pdf, ocr, path, index, lang, dpi, force_ocr,
                            layout_backend if read_layout else None)
        report.pages.append(page)
        if on_page:
            on_page(page)
    return report


def _layout_headings(path: str, index: int, layout_backend) -> list[str]:
    """Titres issus des bandes colorées de la page, s'ils sont lisibles.

    Ils sont placés en tête du rendu Markdown : la maquette compose justement
    ces bandes en tête d'annonce, et leur rôle est balisé pour que la
    classification des titres n'ait rien à deviner.
    """
    if layout_backend is None:
        return []
    try:
        from ..core.layout import bands_on_page, bands_to_markdown

        bands = bands_on_page(
            layout_backend.page_fills(path, index),
            layout_backend.page_words(path, index),
            index + 1,
        )
    except Exception:
        # La maquette est un bonus : son échec ne doit pas coûter la page.
        return []
    return bands_to_markdown(bands).get(index + 1, [])


def _render_text_layer(
    pdf: PdfBackend, path: str, index: int, plain: str
) -> tuple[str, bool]:
    """Rend la couche de texte en Markdown, titres promus si possible.

    Retourne (markdown, titres_promus). Un backend sans ``page_lines`` ne donne
    pas la mise en forme : on rend alors le texte brut et on le dit, car sans
    titres le tri des annonces perd son principal discriminant.
    """
    lecteur = getattr(pdf, "page_lines", None)
    if lecteur is None:
        return plain, False
    try:
        lines = lecteur(path, index)
    except Exception:
        # La mise en forme est un bonus : son échec ne doit pas coûter la page.
        return plain, False
    if not lines:
        return plain, False
    return lines_to_markdown(lines), True


def _extract_one(
    pdf: PdfBackend,
    ocr: OcrBackend | None,
    path: str,
    index: int,
    lang: str,
    dpi: int,
    force_ocr: bool,
    layout_backend=None,
) -> PageText:
    text, rendu, confidence, note = "", "", 0.0, ""
    entetes = _layout_headings(path, index, layout_backend)
    if not force_ocr:
        try:
            text = pdf.page_text(path, index) or ""
        except Exception as exc:  # PDF partiellement corrompu : page isolée, pas tout le document
            return PageText(index, "", "erreur", 0.0, note=f"lecture impossible : {exc}")
        confidence, note = text_layer_confidence(text)
        # La confiance se juge sur le texte brut ; c'est le rendu Markdown, avec
        # ses titres promus, qui part vers la suite du pipeline.
        rendu, promu = _render_text_layer(pdf, path, index, text)
        if entetes:
            # Les bandes portent déjà leur texte dans le corps de la page ; les
            # ajouter en tête le duplique, mais c'est ce doublon qui donne à la
            # segmentation un titre dont le rôle est certain.
            rendu = "\n".join([*entetes, "", rendu])
            note += f" — {len(entetes)} bande(s) de maquette lue(s)"
        elif not promu:
            note += " — mise en forme indisponible, titres non promus"

    needs_ocr = force_ocr or confidence < 0.6
    if needs_ocr:
        if ocr is None:
            return PageText(
                index, rendu or text, "texte_integre" if text.strip() else "vide", confidence,
                char_count=len(text),
                note=note + " — OCR souhaitable mais aucun moteur OCR installé "
                            "(pip install pytesseract pillow + tesseract-ocr-fra)",
            )
        try:
            image = pdf.page_image(path, index, dpi)
            ocr_text, ocr_conf = ocr.image_to_text(image, lang)
        except Exception as exc:
            return PageText(
                index, rendu or text, "texte_integre" if text.strip() else "erreur", confidence,
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
            index, rendu or text, "texte_integre", max(confidence, 0.6), char_count=len(text),
            note=f"{note} — OCR tenté, sans gain",
        )

    return PageText(index, rendu or text, "texte_integre", confidence,
                    char_count=len(text), note=note)
