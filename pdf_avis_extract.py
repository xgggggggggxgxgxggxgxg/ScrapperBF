"""
Segmentation des AVIS d'un Quotidien DGCMEF en BLOCS de texte.

IMPORTANT — plus d'extraction regex des champs. Ce module ne fait QUE de la
géométrie / segmentation : il localise chaque annonce et renvoie son TEXTE
intégral (émetteur, objet, corps) + sa provenance (bulletin, pages). Les champs
(émetteur, objet, montant, dates, lots…) sont extraits exclusivement par le
consensus de 3 modèles LLM (voir consensus_extract.py).

Repère de découpage : l'en-tête de type (« Avis de demande de prix », « Avis
d'appel d'offres ouvert »…) suivi d'une référence / d'un financement. Chaque bloc
démarre quelques lignes AVANT l'en-tête pour inclure l'émetteur et l'objet qui le
précèdent, afin que le LLM dispose de tout le contexte.
"""

import re
import sys
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("scrapperbf.avis")

# En-tête de type (repère STRUCTUREL de début d'annonce, pas un champ extrait).
TYPE_PHRASE = (
    r"(avis\s+de\s+demande\s+de\s+prix(?:\s+(?:à|a)\s+commande?s?|\s+acc[ée]l[ée]r[ée]e?)?"
    r"|avis\s+d[’'`]?\s*appel\s+d[’'`]?\s*offres(?:\s+ouvert|\s+acc[ée]l[ée]r[ée]e?|\s+direct)?(?:\s+(?:à|a)\s+commande?s?)?"
    r"|avis\s+(?:g[ée]n[ée]ral\s+)?(?:à|a)\s+manifestation\s+d[’'`]?\s*int[ée]r[êe]t"
    r"|avis\s+de\s+sollicitation\s+de\s+manifestation\s+d[’'`]?\s*int[ée]r[êe]t"
    r"|sollicitation\s+de\s+manifestation\s+d[’'`]?\s*int[ée]r[êe]t"
    r"|avis\s+de\s+recrutement"
    r"|avis\s+de\s+demande\s+de\s+propositions"
    r"|demande\s+de\s+propositions)"
)
TYPE_HEAD_RE = re.compile(r"^\s*" + TYPE_PHRASE, re.I)
# Confirme qu'un en-tête ouvre un vrai avis (et n'est pas une simple mention).
_GUARD_RE = re.compile(r"financement|participation|n[°o]\s*:?\s*\d", re.I)
STOP_LINE_RE = re.compile(r"www\.dgcmef|^\s*\d{1,3}\s*$|quotidien\s+n", re.I)

# --- Taxonomie hiérarchique (comme côté RÉSULTATS) ------------------------
# Niveau 1 : macro-section. Les en-têtes décoratifs « APPELS D'OFFRES DES … »
# ne sont pas toujours du texte sélectionnable ; on bascule donc sur RÉGIONS
# dès qu'un émetteur « RÉGION/COMMUNE/… » apparaît (même repère que RÉSULTATS).
MACRO_MINISTERES = "MINISTERES"
MACRO_REGIONS = "REGIONS"
_EMETTEUR_REGION_RE = re.compile(
    r"\b(R[ÉE]GION\s+(?:DU|DE|DES|D['’])|CONSEIL\s+R[ÉE]GIONAL|COMMUNE\s+(?:DE|DU|RURALE|URBAINE)"
    r"|MAIRIE|CONSEIL\s+MUNICIPAL|PROVINCE\s+DU|AUTORIT[ÉE]S?\s+R[ÉE]GIONALES?)\b", re.I)
_MACRO_HDR_RE = re.compile(
    r"appels?\s+d['’\s]*offres?\s+des\s+(minist[èe]res?|r[ée]gions?|collectivit[ée]s?)", re.I)

# Niveau 2 : catégorie de marché. Bandeaux de section (souvent du texte
# sélectionnable) : Travaux / Fournitures et services / Prestations intellectuelles.
_CAT_PATTERNS = [
    ("travaux", re.compile(r"^\s*travaux\s*$", re.I)),
    ("fournitures", re.compile(r"^\s*f(?:ou|u)rni?tures?\b.*?(services?|courants?)?\s*$", re.I)),
    ("prestations_intellectuelles",
     re.compile(r"^\s*prestations?\s+intellectuelles?", re.I)),
    ("prestations_intellectuelles",
     re.compile(r"^\s*services?\s+de\s+consultants?\s*$", re.I)),
]


# --- Détection par COULEUR des bandeaux (détecteur principal) --------------
# Sémantique confirmée visuellement sur les bulletins :
#   ROUGE  -> bandeau de CATÉGORIE (Fournitures / Travaux / Prestations…)
#   JAUNE  -> bandeau d'ÉMETTEUR (autorité contractante)
#   CYAN   -> bandeau d'OBJET = DÉBUT d'une annonce (hérite émetteur + catégorie)
def is_red(c) -> bool:
    if not isinstance(c, (list, tuple)) or len(c) < 3:
        return False
    r, g, b = c[0], c[1], c[2]
    return r > 0.75 and g < 0.42 and b < 0.42


def is_yellow(c) -> bool:
    if not isinstance(c, (list, tuple)) or len(c) < 3:
        return False
    r, g, b = c[0], c[1], c[2]
    return r > 0.72 and g > 0.72 and b < 0.5


def is_cyan(c) -> bool:
    if not isinstance(c, (list, tuple)) or len(c) < 3:
        return False
    r, g, b = c[0], c[1], c[2]
    return r < 0.6 and g > 0.6 and b > 0.6


def classify_band_color(color) -> Optional[str]:
    """Rôle d'un bandeau d'après sa couleur de remplissage. None si neutre."""
    if is_red(color):
        return "categorie"
    if is_yellow(color):
        return "emetteur"
    if is_cyan(color):
        return "objet"
    return None


_LOTS_IN_OBJET_RE = re.compile(r"\(\s*(\d{1,2})\s*lots?\s*\)", re.I)


def _category_label(text: str) -> Optional[str]:
    """Catégorie normalisée à partir du texte d'un bandeau rouge."""
    s = (text or "").strip()
    if re.search(r"prestations?\s+intellectuelles?|consultants?", s, re.I):
        return "prestations_intellectuelles"
    # Tolérance aux fautes réelles du bulletin : « Furnitures » (sans « o »),
    # « Fourntures »… -> on accepte f(ou|u)rni?tures.
    if re.search(r"f(?:ou|u)rni?tures?", s, re.I):
        return "fournitures"
    if re.search(r"travaux", s, re.I):
        return "travaux"
    return None


def plan_annonces(bands: list) -> list:
    """LOGIQUE PURE (testable sans PDF). À partir des bandeaux ordonnés
    (page, top) — chacun {kind, text, page, top} —, produit une annonce par
    bandeau CYAN (objet), en héritant du dernier émetteur (jaune), de la
    dernière catégorie (rouge) et de la macro-section courante. `_idx` pointe
    sur l'index du bandeau objet dans `bands` (pour borner le texte ensuite)."""
    cur_cat = cur_em = None
    macro = MACRO_MINISTERES
    out = []
    for i, b in enumerate(bands):
        k = b.get("kind")
        if k == "categorie":
            cur_cat = _category_label(b.get("text"))
        elif k == "emetteur":
            cur_em = b.get("text")
            macro = _macro_from_emetteur(cur_em, macro)
        elif k == "objet":
            objet = b.get("text") or ""
            m = _LOTS_IN_OBJET_RE.search(objet)
            out.append({
                "objet": objet, "emetteur": cur_em, "categorie": cur_cat,
                "macro_section": macro,
                "nb_lots_declares": int(m.group(1)) if m else None,
                # Confirmation par TRIPLET : une annonce est sûre quand on a vu,
                # au-dessus du titre cyan, l'émetteur (jaune) ET la catégorie
                # (rouge). Sinon : contexte incomplet -> doute / revue humaine.
                "triplet_complet": bool(cur_em and cur_cat),
                "contexte_manquant": [n for n, v in (("categorie", cur_cat),
                                                     ("emetteur", cur_em)) if not v],
                "_idx": i,
            })
    return out


def _macro_from_emetteur(text: str, current: str) -> str:
    """Bascule DÉFINITIVE sur RÉGIONS dès qu'un nom d'émetteur de collectivité
    apparaît (irréversible dans l'ordre du bulletin)."""
    if current == MACRO_REGIONS:
        return MACRO_REGIONS
    return MACRO_REGIONS if _EMETTEUR_REGION_RE.search(text or "") else current


def _detect_category(line: str) -> Optional[str]:
    """Reconnaît un bandeau de catégorie (ligne courte isolée). None sinon."""
    s = line.strip()
    if not s or len(s) > 48:
        return None
    for cat, rx in _CAT_PATTERNS:
        if rx.match(s):
            return cat
    return None

# Champs métier (pour la couverture / le juge) — désormais remplis par le LLM.
CORE_FIELDS = ["autorite_emettrice", "objet", "type_avis", "numero_reference",
               "financement", "date_limite_depot"]

CONTEXT_LINES = 8  # lignes avant l'en-tête, pour inclure émetteur + objet


def norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _in_range(i: int, page_range) -> bool:
    """i (0-based) est-il dans la zone [start..end] inclusive ? None = tout."""
    return page_range is None or page_range[0] <= i <= page_range[1]


def _lines_with_pages(pdf, page_range=None) -> list:
    out = []
    for i, pg in enumerate(pdf.pages):
        if not _in_range(i, page_range):
            continue
        for ln in (pg.extract_text() or "").split("\n"):
            out.append((i + 1, ln.rstrip()))
    return out


def _numero_label(path, numero, label):
    if numero is None or label is None:
        name = Path(path).stem.replace("_", " ")
        m = re.search(r"quotidien\s*n?[°o]?\s*(\d{3,4})\s*(bis)?", name, re.I)
        core = (m.group(1) + ("bis" if m.group(2) else "")) if m else Path(path).stem
        numero = numero or ("N" + core)
        label = label or ("Quotidien N°" + core)
    return numero, label


def segment_avis_blocks(path: str, numero: Optional[str] = None,
                        label: Optional[str] = None, page_range=None) -> list:
    """Découpe le PDF en blocs d'annonces (texte + provenance), sans extraire de
    champ. Chaque bloc : {flat_text, _page_debut, _page_fin, _numero, _source_pdf,
    _source}.

    `page_range` (tuple 0-based inclusif (start,end), ou None) restreint la
    segmentation à la zone AVIS (voir section_split.split_sections)."""
    import pdfplumber  # dépendance optionnelle, isolée

    numero, label = _numero_label(path, numero, label)
    prov = {"_numero": numero, "_source_pdf": label, "_source": Path(path).name}
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages
        last_page = page_range[1] if page_range else len(pages) - 1
        bands = _collect_bands(pages, page_range)
        n_objet = sum(1 for b in bands if b["kind"] == "objet")
        if n_objet >= 1:
            # Détecteur PRINCIPAL : couleur + taille des bandeaux.
            blocks = _blocks_from_bands(pages, bands, prov, last_page)
            mode = "couleur"
        else:
            # Repli : ancres texte (bulletins sans bandeaux couleur lisibles).
            blocks = _blocks_from_text(_lines_with_pages(pdf, page_range), prov, path)
            mode = "texte"

    from collections import Counter
    log.info("segmentation AVIS %s [%s] : %d bloc(s) — macro=%s catégorie=%s",
             label, mode, len(blocks),
             dict(Counter(b["_macro_section"] for b in blocks)),
             dict(Counter(b["_categorie"] for b in blocks)))
    return blocks


def _collect_bands(pages, page_range=None) -> list:
    """Bandeaux couleur (rouge/jaune/cyan) pleine largeur, ordonnés (page, top)
    et dédupliqués (un bandeau peut être dessiné en remplissage + bordure)."""
    from pdf_resultats_extract import _crop_text  # réutilise la même extraction
    bands = []
    for pi, pg in enumerate(pages):
        if not _in_range(pi, page_range):
            continue
        w = pg.width
        for r in pg.rects:
            if r["height"] < 6:
                continue
            kind = classify_band_color(r.get("non_stroking_color"))
            if not kind:
                continue
            # Largeur minimale PAR TYPE : le bandeau rouge « catégorie » est un
            # titre CENTRÉ dont la largeur dépend du texte (« Travaux » ≈ 0.40,
            # « Prestations intellectuelles » ≈ 0.50) ; émetteur (jaune) et objet
            # (cyan) sont des bandeaux PLEINE largeur.
            min_frac = 0.30 if kind == "categorie" else 0.45
            if r["width"] < w * min_frac:
                continue
            txt = _crop_text(pg, r)
            if len(txt) < 3:
                continue
            bands.append({"page": pi, "top": r["top"], "bottom": r["bottom"],
                          "kind": kind, "text": txt})
    bands.sort(key=lambda b: (b["page"], b["top"]))
    seen, out = set(), []
    for b in bands:
        key = (b["page"], round(b["top"] / 4), b["text"][:30])
        if key in seen:
            continue
        seen.add(key)
        out.append(b)
    return out


def _blocks_from_bands(pages, bands: list, prov: dict, last_page=None) -> list:
    """Assemble une annonce par bandeau CYAN : objet + émetteur + catégorie
    hérités, texte borné jusqu'au bandeau suivant (quel qu'il soit). `last_page`
    (0-based) borne la dernière annonce à la fin de la zone AVIS."""
    from pdf_resultats_extract import _text_between
    if last_page is None:
        last_page = len(pages) - 1
    blocks = []
    for a in plan_annonces(bands):
        i = a["_idx"]
        ob = bands[i]
        nxt = bands[i + 1] if i + 1 < len(bands) else None
        p0, t0 = ob["page"], ob["bottom"]
        p1 = nxt["page"] if nxt else last_page
        t1 = nxt["top"] if nxt else pages[p1].height
        body = _text_between(pages, p0, t0, p1, t1)
        em, obj = a["emetteur"], a["objet"]
        text = norm(" ".join(x for x in (em, obj, body) if x))[:4500]
        # Page de fin : la page précédant le bandeau suivant s'il ouvre une page.
        pg_fin = (p0 if (nxt and nxt["page"] != p0 and nxt["top"] < 60) else p1) + 1
        blocks.append({
            "flat_text": text,
            "_page_debut": p0 + 1, "_page_fin": max(p0 + 1, pg_fin),
            "_macro_section": a["macro_section"], "_categorie": a["categorie"],
            "_emetteur_bandeau": em, "_objet_bandeau": obj,
            "_nb_lots_declares": a["nb_lots_declares"], "_detecteur": "couleur",
            "_triplet_complet": a["triplet_complet"],
            "_contexte_manquant": a["contexte_manquant"],
            **prov,
        })
    return blocks


def _blocks_from_text(lines: list, prov: dict, path: str) -> list:
    """Repli : segmentation par ancres texte (en-tête de type) + report en avant
    de la taxonomie. Utilisé quand aucun bandeau couleur n'est détecté."""
    macro_at, cat_at = [], []
    macro, cat = MACRO_MINISTERES, None
    for pg, ln in lines:
        m = _MACRO_HDR_RE.search(ln)
        if m:
            macro = MACRO_REGIONS if re.search(r"r[ée]gion|collectivit", m.group(1), re.I) else MACRO_MINISTERES
        macro = _macro_from_emetteur(ln, macro)
        c = _detect_category(ln)
        if c:
            cat = c
        macro_at.append(macro)
        cat_at.append(cat)

    anchors = []
    for idx, (pg, ln) in enumerate(lines):
        s = ln.strip()
        if not TYPE_HEAD_RE.match(s):
            continue
        if len(s) > 85 and not re.search(r"\bn[°o]\b", s):
            continue
        window = " ".join(x[1] for x in lines[idx: idx + 10])
        if _GUARD_RE.search(window):
            anchors.append(idx)

    blocks = []
    for i, idx in enumerate(anchors):
        start = max(0, idx - CONTEXT_LINES)
        end = (anchors[i + 1] - CONTEXT_LINES) if i + 1 < len(anchors) else min(idx + 120, len(lines))
        end = max(end, idx + 1)
        pg_debut = lines[idx][0]
        pg_fin = lines[min(end - 1, len(lines) - 1)][0]
        text = " ".join(x[1] for x in lines[start:end] if not STOP_LINE_RE.search(x[1]))
        blk_macro = _macro_from_emetteur(text, macro_at[idx])
        blocks.append({
            "flat_text": norm(text)[:4500],
            "_page_debut": pg_debut, "_page_fin": max(pg_debut, pg_fin),
            "_macro_section": blk_macro, "_categorie": cat_at[idx],
            "_detecteur": "texte", **prov,
        })
    return blocks


# Alias de compatibilité : renvoie les blocs (sans champs regex).
extract_avis_from_pdf = segment_avis_blocks


def enrich_coverage(avis: dict) -> dict:
    """Indicateur de complétude : part des champs clés renseignés (par le LLM).
    Les champs vides sont signalés « à vérifier ». Agnostique de la source."""
    found = [k for k in CORE_FIELDS if avis.get(k)]
    manquants = [k for k in CORE_FIELDS if not avis.get(k)]
    avis["_score_exactitude"] = round(100 * len(found) / len(CORE_FIELDS))
    avis["_champs_absents"] = []
    avis["_champs_a_verifier"] = manquants
    avis.setdefault("_mode", "llm")
    avis["_analyse"] = [
        {"champ": k, "statut": "complete", "confiance": None,
         "justification": "renseigné par extraction"} for k in found
    ]
    return avis


if __name__ == "__main__":
    from logging_config import setup_logging
    setup_logging()
    if len(sys.argv) < 2:
        print("Usage: python pdf_avis_extract.py <pdf> [...]  # segmente en blocs (pas d'extraction)")
        sys.exit(1)
    total = 0
    out = {"AVIS_BLOCS": []}
    for p in sys.argv[1:]:
        blocks = segment_avis_blocks(p)
        total += len(blocks)
        out["AVIS_BLOCS"].extend(blocks)
    Path("avis_blocs.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Total : %d blocs -> avis_blocs.json", total)
