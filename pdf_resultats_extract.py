"""
Segmentation de la section RÉSULTATS d'un Quotidien DGCMEF.

Structure (confirmée visuellement sur les bulletins réels) :
  - Deux macro-sections, dans l'ordre :
      1) "RÉSULTATS PROVISOIRES DES MINISTÈRES, INSTITUTIONS ET MAÎTRISES
         D'OUVRAGES DÉLÉGUÉES"
      2) "RÉSULTATS PROVISOIRES DES RÉGIONS" (collectivités territoriales)
  - Pour CHAQUE émetteur (ex. « MINISTERE DE L'ECONOMIE ET DES FINANCES »), une
    BARRE NOIRE pleine largeur, texte blanc : c'est le « cadre noir » qui ouvre
    le tableau de cet émetteur. La barre noire suivante ouvre l'émetteur suivant.
  - À l'intérieur d'un tableau, des BANDES ORANGE « Lot N : … » séparent les lots.
  - Le tableau s'étale souvent sur plusieurs pages ; il n'y a pas de répétition
    d'en-tête -> on ne coupe PAS au changement de page, seulement aux barres noires.

Ce module est 100 % déterministe (pdfplumber, pas de LLM) : il découpe chaque
unité-émetteur (barre noire -> barre noire suivante), compte les lots détectés,
et produit un `flat_text` prêt à être envoyé au LLM (voir resultats_llm.py) pour
structurer le contenu multi-colonnes du tableau.

Les fonctions de bas niveau (is_black, is_orange, detect_macro_section) sont
pures et testées hors-ligne.
"""

import re
import sys
import json
from pathlib import Path
from typing import Optional

MACRO_MINISTERES = "MINISTERES"
MACRO_REGIONS = "REGIONS"

_MINISTERES_RE = re.compile(r"MINIST[ÈE]RES?|INSTITUTIONS|MA[ÎI]TRISES?\s+D.OUVRAGES?", re.I)
_REGIONS_RE = re.compile(r"R[ÉE]GIONS?|COLLECTIVIT[ÉE]S?\s+TERRITORIALES?", re.I)
_RESULTATS_HDR_RE = re.compile(r"R[ÉE]SULTATS\s+PROVISOIRES", re.I)
_LOT_RE = re.compile(r"^\s*lot\s*n?[°o]?\s*\d", re.I)
# Détection des lots par le TEXTE (plus fiable que la bande de couleur, qui n'est
# pas obligatoire) : « Lot 1 : … », « LOT N°2 : … », en début de ligne.
_LOT_TEXT_RE = re.compile(r"(?im)^\s*lot\s*n?[°o]?\s*(\d{1,2})\s*:\s*([^\n]{0,90})")


def find_lot_markers(flat_text: str) -> list:
    """Repère les lots dans le texte aplati. Retourne une liste de libellés
    courts « Lot N : … », dédupliquée par numéro (ordre d'apparition)."""
    out, seen = [], set()
    for m in _LOT_TEXT_RE.finditer(flat_text or ""):
        num = m.group(1)
        if num in seen:
            continue
        seen.add(num)
        label = re.sub(r"\s+", " ", f"Lot {num} : {m.group(2)}").strip()
        out.append(label)
    return out
# Les en-têtes décoratifs de section ne sont pas du texte sélectionnable, mais
# la 2e macro-section (collectivités) se trahit par le nom de l'émetteur :
# « RÉGION DE … », « COMMUNE DE … », « CONSEIL RÉGIONAL … », etc.
_EMETTEUR_REGION_RE = re.compile(
    r"^(R[ÉE]GION|CONSEIL\s+R[ÉE]GIONAL|COMMUNE|MAIRIE|PROVINCE|CONSEIL\s+MUNICIPAL)\b", re.I)


def is_black(color) -> bool:
    """Rectangle à remplissage noir (barre d'émetteur). Accepte le gris scalaire
    0.0 comme les triplets (0,0,0)."""
    if color is None:
        return False
    if isinstance(color, (int, float)):
        return color < 0.12
    try:
        return all(float(v) < 0.12 for v in color[:3])
    except (TypeError, ValueError):
        return False


def is_orange(color) -> bool:
    """Bande orange/or des séparateurs de lot."""
    if not isinstance(color, (list, tuple)) or len(color) < 3:
        return False
    r, g, b = color[0], color[1], color[2]
    return r > 0.9 and 0.5 < g < 0.93 and b < 0.66


def detect_macro_section(page_text: str, current: Optional[str]) -> Optional[str]:
    """Met à jour la macro-section courante d'après l'en-tête décoratif
    « RÉSULTATS PROVISOIRES DES … » de la page (sinon garde l'état courant).

    On ignore la page Sommaire (table des matières), qui cite les DEUX sections
    et fausserait la détection."""
    if not page_text:
        return current
    if re.search(r"\bsommaire\b", page_text[:200], re.I):
        return current
    head = page_text[:400]
    if not _RESULTATS_HDR_RE.search(head):
        return current
    has_min = bool(_MINISTERES_RE.search(head))
    has_reg = bool(_REGIONS_RE.search(head))
    if has_min and not has_reg:
        return MACRO_MINISTERES
    if has_reg and not has_min:
        return MACRO_REGIONS
    return current


def _crop_text(page, rect, pad_bottom: float = 1.0) -> str:
    try:
        c = page.crop((rect["x0"], rect["top"], rect["x1"], rect["bottom"] + pad_bottom))
        return " ".join((c.extract_text() or "").split())
    except Exception:
        return ""


def _bars_and_lots(page, width: float):
    """Retourne (barres_noires, bandes_lot) dédupliquées pour une page."""
    bars, lots = [], []
    for r in page.rects:
        if r["width"] < width * 0.5 or r["height"] < 6:
            continue
        col = r.get("non_stroking_color")
        if is_black(col):
            bars.append(r)
        elif is_orange(col):
            lots.append(r)

    def dedup(rects, want_lot):
        seen, out = set(), []
        for r in sorted(rects, key=lambda r: r["top"]):
            txt = _crop_text(page, r)
            if want_lot and not _LOT_RE.match(txt):
                continue          # bande orange sans « Lot N » = sous-ligne, ignorée
            if not want_lot and len(txt) < 4:
                continue
            key = (round(r["top"] / 4), txt[:40])
            if key in seen:
                continue
            seen.add(key)
            out.append({"top": r["top"], "text": txt})
        return out

    return dedup(bars, False), dedup(lots, True)


def _text_between(pages, p0, top0, p1, top1) -> str:
    """Texte entre deux ancres (page, y), barres noires comprises exclues."""
    parts = []
    for pi in range(p0, p1 + 1):
        pg = pages[pi]
        top = top0 + 10 if pi == p0 else 0
        bottom = top1 if pi == p1 else pg.height
        if bottom <= top:
            continue
        try:
            parts.append(pg.crop((0, top, pg.width, bottom)).extract_text() or "")
        except Exception:
            pass
    return "\n".join(p for p in parts if p).strip()


def segment_resultats(path: str, numero: Optional[str] = None,
                      label: Optional[str] = None, page_range=None) -> list:
    """Découpe la section RÉSULTATS en unités-émetteur (une par barre noire).

    Chaque unité : {emetteur, macro_section, page_debut, page_fin,
    nb_lots_detectes, lots, flat_text, + provenance}.

    `page_range` (tuple 0-based inclusif (start,end), ou None) restreint la
    collecte à la zone RÉSULTATS (voir section_split.split_sections)."""
    import pdfplumber

    if numero is None or label is None:
        name = Path(path).stem.replace("_", " ")
        m = re.search(r"quotidien\s*n?[°o]?\s*(\d{3,4})\s*(bis)?", name, re.I)
        core = (m.group(1) + ("bis" if m.group(2) else "")) if m else Path(path).stem
        numero = numero or ("N" + core)
        label = label or ("Quotidien N°" + core)

    pdf = pdfplumber.open(path)
    pages = pdf.pages
    last_page = page_range[1] if page_range else len(pages) - 1

    # 1) Collecte des barres noires (émetteurs) et des lots, page par page.
    bars = []   # {page, top, emetteur}
    lots = []   # {page, top, text}
    for pi, pg in enumerate(pages):
        if page_range is not None and not (page_range[0] <= pi <= page_range[1]):
            continue
        b, l = _bars_and_lots(pg, pg.width)
        for r in b:
            bars.append({"page": pi, "top": r["top"], "emetteur": r["text"]})
        for r in l:
            lots.append({"page": pi, "top": r["top"], "text": r["text"]})

    # Macro-section : ministères d'abord, puis bascule DÉFINITIVE sur les
    # collectivités dès le premier émetteur « RÉGION/COMMUNE/… » (les en-têtes
    # décoratifs ne sont pas du texte sélectionnable, le nom d'émetteur si).
    macro = MACRO_MINISTERES
    for bar in bars:
        if _EMETTEUR_REGION_RE.match(bar["emetteur"]):
            macro = MACRO_REGIONS
        bar["macro"] = macro

    # 2) Découpage : chaque barre -> unité jusqu'à la barre suivante.
    units = []
    for i, bar in enumerate(bars):
        nxt = bars[i + 1] if i + 1 < len(bars) else None
        p1 = nxt["page"] if nxt else last_page
        t1 = nxt["top"] if nxt else pages[p1].height
        flat = _text_between(pages, bar["page"], bar["top"], p1, t1)
        orange_lots = [
            lo for lo in lots
            if (lo["page"], lo["top"]) > (bar["page"], bar["top"])
            and (nxt is None or (lo["page"], lo["top"]) < (nxt["page"], nxt["top"]))
        ]
        # Lots repérés par le TEXTE (fiable), complétés par les bandes orange.
        lots_texte = find_lot_markers(flat)
        nb = max(len(lots_texte), len(orange_lots))
        units.append({
            "emetteur": bar["emetteur"],
            "macro_section": bar["macro"] or MACRO_MINISTERES,
            "page_debut": bar["page"] + 1,
            "page_fin": p1 + 1,
            "nb_lots_detectes": nb,
            "lots": lots_texte or [lo["text"] for lo in orange_lots],
            "nb_lots_bandes_orange": len(orange_lots),
            "flat_text": flat,
            "_numero": numero, "_source_pdf": label, "_source": Path(path).name,
        })
    pdf.close()
    return units


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pdf_resultats_extract.py <pdf> [<pdf> ...]")
        sys.exit(1)
    for p in sys.argv[1:]:
        units = segment_resultats(p)
        from collections import Counter
        by_macro = Counter(u["macro_section"] for u in units)
        tot_lots = sum(u["nb_lots_detectes"] for u in units)
        print(f"\n### {Path(p).name} : {len(units)} émetteurs, {tot_lots} lots "
              f"({dict(by_macro)})")
        for u in units[:8]:
            print(f"  [{u['macro_section'][:4]}] p{u['page_debut']}-{u['page_fin']} "
                  f"| {u['nb_lots_detectes']} lot(s) | {u['emetteur'][:60]}")
