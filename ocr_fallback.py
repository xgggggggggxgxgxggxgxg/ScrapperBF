"""
Repli LightOCR : quand le texte extrait d'une page est CORROMPU (police PDF
cassée, tableau qui renvoie des métadonnées / du charabia), on ré-océrise
l'image de la page et on repart du texte OCR.

Conçu comme les providers LLM : le moteur OCR est un callable INJECTABLE
(img_bytes -> texte), testable hors-ligne avec une doublure. La résolution par
défaut tente pytesseract (si installé) ; sinon None -> le chemin est branché
mais inerte dans cet environnement sans OCR. Dans tous les cas, un bloc passé
par l'OCR est marqué `_alerte_humaine` (révision humaine systématique, comme
demandé dans l'architecture).

Fonctions pures / injectables testées hors-ligne ; le rendu d'image (pdfplumber)
n'est exercé qu'avec un vrai PDF.
"""

import io
import os
import logging
from typing import Optional, Callable

from judge import garbling_score

log = logging.getLogger("scrapperbf.ocr")

# Au-delà de ce taux de charabia, on considère le texte corrompu -> OCR.
OCR_GARBLING_THRESHOLD = 0.22


def needs_ocr(text: str) -> bool:
    """Le texte est-il assez corrompu pour justifier une ré-OCR ?"""
    return garbling_score(text) >= OCR_GARBLING_THRESHOLD


def render_page_image(pdf_path: str, page_no: int, resolution: int = 200) -> bytes:
    """Rend une page (0-based) en PNG via pdfplumber. Exercé seulement avec un
    vrai PDF (pas de test hors-ligne)."""
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        im = pdf.pages[page_no].to_image(resolution=resolution)
        buf = io.BytesIO()
        im.original.save(buf, format="PNG")
        return buf.getvalue()


def _tesseract_provider() -> Optional[Callable]:
    """Backend OCR local (pytesseract) s'il est installé, sinon None."""
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return None

    def run(img_bytes: bytes) -> str:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(img_bytes))
        return pytesseract.image_to_string(img, lang=os.environ.get("OCR_LANG", "fra"))

    return run


def build_ocr_provider() -> Optional[Callable]:
    """Moteur OCR actif selon l'environnement (ENABLE_OCR=0 pour couper).
    None = aucun backend -> le repli OCR est inerte (pas d'erreur)."""
    if os.environ.get("ENABLE_OCR", "1") in ("0", "false", "False", ""):
        return None
    return _tesseract_provider()


def ocr_page(pdf_path: str, page_no: int, provider: Optional[Callable] = None) -> Optional[str]:
    """Rend la page et la fait océriser par le backend. None si pas de backend
    ou en cas d'échec (on ne lève pas : le bloc reste signalé pour revue)."""
    provider = provider if provider is not None else build_ocr_provider()
    if provider is None:
        log.warning("OCR demandé mais aucun backend disponible (page %d)", page_no + 1)
        return None
    try:
        txt = provider(render_page_image(pdf_path, page_no))
        log.info("OCR page %d : %d caractères récupérés", page_no + 1, len(txt or ""))
        return txt
    except Exception as e:  # rendu / OCR cassé -> on renonce proprement
        log.error("OCR page %d échoué : %s", page_no + 1, e)
        return None


def _page0(block: dict) -> int:
    """Index 0-based de la page de début (gère _page_debut et page_debut)."""
    p = block.get("_page_debut") or block.get("page_debut") or 1
    return max(0, int(p) - 1)


def recover_block(block: dict, pdf_path: str, ocr_fn: Optional[Callable] = None) -> dict:
    """Si le texte du bloc est corrompu : tente une ré-OCR de sa page et
    remplace le texte s'il est MOINS corrompu. Marque toujours `_ocr_tente` et
    `_alerte_humaine`. No-op (bloc inchangé) si le texte est propre.

    `ocr_fn(pdf_path, page0) -> texte|None` est injectable (tests hors-ligne) ;
    par défaut = ocr_page avec le backend de l'environnement."""
    text = block.get("flat_text") or block.get("texte_brut") or ""
    if not needs_ocr(text):
        return block

    ocr_fn = ocr_fn or (lambda p, i: ocr_page(p, i))
    ocr_text = ocr_fn(pdf_path, _page0(block))

    out = dict(block)
    out["_ocr_tente"] = True
    out["_alerte_humaine"] = True  # révision humaine systématique après OCR
    if ocr_text and garbling_score(ocr_text) < garbling_score(text):
        out["flat_text"] = ocr_text.strip()[:4500]
        out["_ocr_applique"] = True
        log.info("OCR appliqué (page %d) : charabia %.2f -> %.2f",
                 _page0(block) + 1, garbling_score(text), garbling_score(ocr_text))
    else:
        out["_ocr_applique"] = False
        log.warning("OCR page %d : pas d'amélioration (ou pas de backend) -> revue humaine",
                    _page0(block) + 1)
    return out
