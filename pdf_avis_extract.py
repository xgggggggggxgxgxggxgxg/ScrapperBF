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
    ("fournitures", re.compile(r"^\s*fournitures?\b.*?(services?|courants?)?\s*$", re.I)),
    ("prestations_intellectuelles",
     re.compile(r"^\s*prestations?\s+intellectuelles?", re.I)),
    ("prestations_intellectuelles",
     re.compile(r"^\s*services?\s+de\s+consultants?\s*$", re.I)),
]


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


def _lines_with_pages(pdf) -> list:
    out = []
    for i, pg in enumerate(pdf.pages):
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
                        label: Optional[str] = None) -> list:
    """Découpe le PDF en blocs d'annonces (texte + provenance), sans extraire de
    champ. Chaque bloc : {flat_text, _page_debut, _page_fin, _numero, _source_pdf,
    _source}."""
    import pdfplumber  # dépendance optionnelle, isolée

    numero, label = _numero_label(path, numero, label)
    with pdfplumber.open(path) as pdf:
        lines = _lines_with_pages(pdf)

    # Passe 1 — état taxonomique courant ligne par ligne (report en avant des
    # en-têtes de macro-section et de catégorie, exactement comme RÉSULTATS).
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

    # Passe 2 — ancres de début d'annonce (en-tête de type confirmé).
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
        # Taxonomie : état au niveau de l'en-tête, consolidé par le texte du bloc
        # (l'émetteur « RÉGION/COMMUNE… » peut n'apparaître que dans le contexte).
        blk_macro = _macro_from_emetteur(text, macro_at[idx])
        blocks.append({
            "flat_text": norm(text)[:4500],
            "_page_debut": pg_debut, "_page_fin": max(pg_debut, pg_fin),
            "_macro_section": blk_macro, "_categorie": cat_at[idx],
            "_numero": numero, "_source_pdf": label, "_source": Path(path).name,
        })

    from collections import Counter
    log.info("segmentation AVIS %s : %d bloc(s) — macro=%s catégorie=%s", label, len(blocks),
             dict(Counter(b["_macro_section"] for b in blocks)),
             dict(Counter(b["_categorie"] for b in blocks)))
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
