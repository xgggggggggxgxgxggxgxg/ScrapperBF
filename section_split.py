"""
Étape 1 du pipeline : SÉPARER le PDF en deux zones de pages — AVIS (appels
d'offres) et RÉSULTATS — avant toute segmentation fine.

Pourquoi : les deux segmenteurs scannaient tout le PDF et se reposaient sur leurs
seuls marqueurs (bandeaux couleur pour les AVIS, barres noires pour les
RÉSULTATS). Risque de contamination croisée (une barre noire décorative en zone
AVIS, un titre « Avis de… » cité en zone RÉSULTATS). On fixe donc une FRONTIÈRE
de pages, et chaque segmenteur ne travaille que sur sa zone.

Méthode (déterministe, pdfplumber, pas de LLM) : on étiquette chaque page
  R = page de RÉSULTATS  (en-tête « RÉSULTATS PROVISOIRES » ou barre noire)
  A = page d'AVIS        (bandeau couleur cyan/jaune/rouge ou en-tête de type)
  N = neutre (sommaire, couverture…)
puis la zone RÉSULTATS = l'intervalle contigu [1re R .. dernière R] et la zone
AVIS = le complément. Robuste à l'ordre (AVIS avant ou après) et à un marqueur
isolé. Repli sûr : si aucune page R, pas de découpe (comportement d'avant).

La logique de partition `_section_ranges` est pure et testée hors-ligne.
"""

import logging
from typing import Optional

from pdf_resultats_extract import is_black, _RESULTATS_HDR_RE
from pdf_avis_extract import classify_band_color, TYPE_HEAD_RE

log = logging.getLogger("scrapperbf.split")


def _page_has_black_bar(page) -> bool:
    w = page.width
    for r in page.rects:
        if r["width"] >= w * 0.5 and r["height"] >= 6 and is_black(r.get("non_stroking_color")):
            return True
    return False


def _page_has_color_band(page) -> bool:
    w = page.width
    for r in page.rects:
        if r["width"] >= w * 0.45 and r["height"] >= 6 and classify_band_color(r.get("non_stroking_color")):
            return True
    return False


def _label_page(text: str, has_black: bool, has_band: bool) -> str:
    """Étiquette R / A / N d'une page (le sommaire ne compte pas)."""
    head = (text or "")[:400]
    if "sommaire" in head[:200].lower():
        return "N"
    is_r = bool(_RESULTATS_HDR_RE.search(head)) or has_black
    is_a = has_band or bool(_type_head_in(text))
    if is_r and not is_a:
        return "R"
    if is_a and not is_r:
        return "A"
    if is_r and is_a:
        # Page mixte : on tranche par le signal le plus structurant (barre noire
        # = tableau de résultats) sinon AVIS.
        return "R" if has_black else "A"
    return "N"


def _type_head_in(text: str) -> bool:
    for ln in (text or "").split("\n"):
        if TYPE_HEAD_RE.match(ln.strip()):
            return True
    return False


def _section_ranges(labels: list) -> dict:
    """PUR : à partir des étiquettes de page, renvoie les intervalles 0-based
    inclusifs {'AVIS': (s,e)|None, 'RESULTATS': (s,e)|None}.

    RÉSULTATS = intervalle contigu [1re R .. dernière R] ; AVIS = le complément
    (pris comme l'intervalle de ses pages). Sans page R : tout en AVIS."""
    n = len(labels)
    r_idx = [i for i, l in enumerate(labels) if l == "R"]
    if not r_idx:
        return {"AVIS": (0, n - 1) if n else None, "RESULTATS": None}
    r0, r1 = min(r_idx), max(r_idx)
    avis = [i for i in range(n) if i < r0 or i > r1]
    avis_range = (min(avis), max(avis)) if avis else None
    return {"AVIS": avis_range, "RESULTATS": (r0, r1)}


def split_sections(pdf_path: str) -> dict:
    """Ouvre le PDF, étiquette chaque page et renvoie les zones de pages.

    Retour : {'AVIS': (start,end)|None, 'RESULTATS': (start,end)|None,
              'labels': 'AAR…', 'n_pages': int}  (indices 0-based inclusifs)."""
    import pdfplumber
    labels = []
    with pdfplumber.open(pdf_path) as pdf:
        for pg in pdf.pages:
            txt = pg.extract_text() or ""
            labels.append(_label_page(txt, _page_has_black_bar(pg), _page_has_color_band(pg)))
    ranges = _section_ranges(labels)
    ranges["labels"] = "".join(labels)
    ranges["n_pages"] = len(labels)
    log.info("split sections : %d pages — AVIS=%s RÉSULTATS=%s [%s]",
             len(labels), ranges["AVIS"], ranges["RESULTATS"], ranges["labels"])
    return ranges
