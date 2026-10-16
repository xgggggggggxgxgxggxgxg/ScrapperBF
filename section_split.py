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

import re
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
              'methode': 'sommaire'|'marqueurs', 'n_pages': int, ...}
    (indices 0-based inclusifs).

    Stratégie : on tente d'ABORD le SOMMAIRE (lecture des « P. 12 ») ; si le
    sommaire est absent/illisible ou donne un découpage incohérent, on retombe
    sur l'étiquetage par marqueurs (barres noires / bandeaux couleur)."""
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        n = len(pdf.pages)
        # 1) SOMMAIRE
        som = _find_sommaire_text(pdf)
        if som:
            entries = parse_sommaire(som)
            secs = sommaire_section_pages(entries)
            rng = sommaire_ranges(secs, _calibrate_delta(pdf, secs), n)
            if rng and _ranges_sane(rng, n):
                rng.update(methode="sommaire", n_pages=n, sommaire=secs)
                log.info("split [sommaire] : %d pages — AVIS=%s RÉSULTATS=%s (imprimé %s)",
                         n, rng["AVIS"], rng["RESULTATS"], secs)
                return rng
            log.info("split : sommaire illisible/incohérent -> repli marqueurs")
        # 2) MARQUEURS (repli)
        labels = [_label_page(pg.extract_text() or "", _page_has_black_bar(pg),
                              _page_has_color_band(pg)) for pg in pdf.pages]
    ranges = _section_ranges(labels)
    ranges.update(methode="marqueurs", labels="".join(labels), n_pages=len(labels))
    log.info("split [marqueurs] : %d pages — AVIS=%s RÉSULTATS=%s [%s]",
             len(labels), ranges["AVIS"], ranges["RESULTATS"], ranges["labels"])
    return ranges


# ---------------------------------------------------------------- sommaire
_PAGE_REF = re.compile(r"(?<!b\.)\bp\.?\s*(\d{1,4})\b", re.I)   # « P. 12 », « p 12 » (évite B.P.)
_CTX_RESULTATS = re.compile(r"r[ée]sultats|d[ée]pouillements", re.I)
_CTX_AVIS = re.compile(r"\bavis\b|appels?\s+d['’\s]*offres", re.I)


def parse_sommaire(text: str) -> list:
    """PUR : lignes du sommaire portant une référence de page → liste
    {context: 'AVIS'|'RESULTATS'|None, page: int (numéro IMPRIMÉ)}. Le contexte
    bascule quand une ligne d'en-tête « RÉSULTATS… » ou « AVIS… » est vue."""
    ctx = None
    out = []
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if _CTX_RESULTATS.search(line):
            ctx = "RESULTATS"
        elif _CTX_AVIS.search(line):
            ctx = "AVIS"
        for m in _PAGE_REF.finditer(line):
            out.append({"context": ctx, "page": int(m.group(1))})
    return out


def sommaire_section_pages(entries: list) -> dict:
    """PUR : étendue des pages IMPRIMÉES par section (min..max)."""
    a = [e["page"] for e in entries if e["context"] == "AVIS"]
    r = [e["page"] for e in entries if e["context"] == "RESULTATS"]
    return {"AVIS": (min(a), max(a)) if a else None,
            "RESULTATS": (min(r), max(r)) if r else None}


def _to_index(printed: int, delta: int, n: int) -> int:
    """Page imprimée → index PDF 0-based, via l'offset constant delta."""
    return max(0, min(n - 1, printed + delta))


def sommaire_ranges(secs: dict, delta: Optional[int], n: int):
    """PUR : zones (index PDF) à partir des étendues imprimées et de l'offset
    constant `delta` (= index_pdf - page_imprimée). La frontière est le début de
    la section qui vient en second. None si offset inconnu ou rien d'exploitable."""
    if delta is None:
        return None
    a, r = secs.get("AVIS"), secs.get("RESULTATS")
    a0 = _to_index(a[0], delta, n) if a else None
    r0 = _to_index(r[0], delta, n) if r else None
    if a0 is not None and r0 is not None:
        if a0 <= r0:
            return {"AVIS": (a0, max(a0, r0 - 1)), "RESULTATS": (r0, n - 1)}
        return {"RESULTATS": (r0, max(r0, a0 - 1)), "AVIS": (a0, n - 1)}
    if a0 is not None:
        return {"AVIS": (a0, n - 1), "RESULTATS": None}
    if r0 is not None:
        return {"AVIS": None, "RESULTATS": (r0, n - 1)}
    return None


def _ranges_sane(rng: dict, n: int) -> bool:
    """Garde-fou : au moins une zone, bornes valides et ordonnées."""
    ok = False
    for z in ("AVIS", "RESULTATS"):
        v = rng.get(z)
        if v is None:
            continue
        s, e = v
        if not (0 <= s <= e <= n - 1):
            return False
        ok = True
    return ok


def _looks_like_toc(text: str) -> bool:
    """Une page est le SOMMAIRE si elle porte le mot « sommaire », OU si elle
    cumule plusieurs renvois « P. N » avec les deux contextes RÉSULTATS et AVIS
    (cas réel DGCMEF : le sommaire n'écrit pas toujours « SOMMAIRE »)."""
    if not text:
        return False
    head = text[:1600]
    refs = len(_PAGE_REF.findall(head))
    if re.search(r"sommaire", head[:400], re.I) and refs >= 2:
        return True
    return refs >= 3 and bool(_CTX_RESULTATS.search(head)) and bool(_CTX_AVIS.search(head))


def _find_sommaire_text(pdf) -> Optional[str]:
    """Texte de la page Sommaire. Dans les Quotidiens DGCMEF, c'est TOUJOURS la
    page 2 (index 1) — on la prend en priorité dès qu'elle porte des renvois
    « P. N ». Repli : balayage des premières pages par signature de TOC (au cas
    où un bulletin décalerait la pagination)."""
    if len(pdf.pages) >= 2:
        t2 = pdf.pages[1].extract_text() or ""
        if _PAGE_REF.search(t2):
            return t2
    for pg in pdf.pages[:6]:
        t = pg.extract_text() or ""
        if _looks_like_toc(t):
            return t
    return None


def _calibrate_delta(pdf, secs: dict) -> Optional[int]:
    """Offset constant delta = index_pdf - page_imprimée, calibré sur un ANCRE
    de contenu fiable : la 1re barre noire (début des RÉSULTATS) correspond à la
    1re page imprimée des RÉSULTATS. Repli sur le 1er bandeau couleur (AVIS)."""
    r = secs.get("RESULTATS")
    if r:
        fb = next((i for i, pg in enumerate(pdf.pages) if _page_has_black_bar(pg)), None)
        if fb is not None:
            return fb - r[0]
    a = secs.get("AVIS")
    if a:
        fc = next((i for i, pg in enumerate(pdf.pages) if _page_has_color_band(pg)), None)
        if fc is not None:
            return fc - a[0]
    return None
