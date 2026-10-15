"""
Extraction déterministe des AVIS d'un Quotidien DGCMEF, directement depuis le PDF.

C'est une "première passe" SANS LLM ni OCR externe : pdfplumber lit le texte,
on localise la section AVIS, on découpe chaque avis sur son en-tête de type
("Avis de demande de prix", "Avis d'appel d'offres ouvert"…) et on extrait par
regex les champs lisibles (émetteur, objet, type, référence, financement,
montant, délai, date limite de dépôt) + la provenance (bulletin / page).

Les champs non trouvés restent None et sont signalés dans `_champs_a_verifier`
(voir enrich_coverage) : honnête plutôt que creux. La couche 4 (révision LLM)
peut ensuite confirmer/compléter tout ça.

Les fonctions utilitaires (trim_reference, detect_type, is_emetteur, find_depot,
split_financement) sont pures et testées hors-ligne dans tests/.
"""

import re
import sys
import json
from pathlib import Path
from typing import Optional


# --- Marqueurs de type d'avis (en-tête qui démarre une nouvelle annonce) ----
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
CAT_RE = re.compile(r"^(fournitures?\s+et\s+services?\s+courants?|travaux|prestations?\s+intellectuelles?)\s*$", re.I)
REF_RE = re.compile(r"\bn[°o]\s*:?\s*([0-9][0-9A-Za-zÀ-ÿ/\-\._ ]{3,70})", re.I)
REF_STOP_RE = re.compile(r"\s+(financement|montant|identifiant|ma[îi]tre|la\s+participation|cet\s+avis|date|r[ée]f[ée]rence|objet)\b", re.I)
FIN_RE = re.compile(r"financement\s*:?\s*(.+)", re.I)
FIN_STOP_RE = re.compile(r"\s+(?:Identifiant|Cet\s+avis|La\s+participation|Montant\s+pr[ée]visionnel|fait\s+suite|\d+\.\s)", re.I)
MONTANT_RE = re.compile(r"montant\s+pr[ée]visionnel[^:]*:?\s*(.+)", re.I)
DELAI_RE = re.compile(r"d[ée]lai\s+d[’'`]?\s*ex[ée]cution[^:]*:?\s*(.+)", re.I)
AC_RE = re.compile(r"identifiant\s+de\s+l[’'`]?\s*autorit[ée]\s+contractante\s*:?\s*(.+)", re.I)
DEPOT_RES = [
    re.compile(r"au\s+plus\s+tard\s+le[^.;\n]{4,55}", re.I),
    # « … devront parvenir … avant le 19/10/2026 à 09 heure »
    re.compile(r"avant\s+le\s+\d{1,2}[/\s][^.;\n]{3,45}", re.I),
    re.compile(r"(?:devront\s+parvenir|d[ée]pos[ée]es?|remises?)[^.;\n]{0,140}?avant\s+le\s+[^.;\n]{4,45}", re.I),
    re.compile(r"date\s+limite[^.;\n]{0,45}?(?:fix[ée]e?\s+)?(?:au|le)\s+[^.;\n]{4,45}", re.I),
    re.compile(r"jusqu[’'`]au\s+[^.;\n]{4,45}", re.I),
    re.compile(r"(?:remise|d[ée]p[ôo]t)\s+des\s+offres[^.;\n]{0,45}?(?:au|le)\s+[^.;\n]{4,45}", re.I),
]
# Montant prévisionnel : souvent DEUX valeurs justes (HTVA puis « soit » TTC).
_MONT_STOP_RE = re.compile(r"\s+(?:Cet\b|La\s+participation|Le\s+pr[ée]sent|\d+\.\s)", re.I)
_MONT_HTVA_RE = re.compile(r"(.+?\bHTVA)\b", re.I)
_MONT_TTC_RE = re.compile(r"soit\s+(.+?\bTTC)\b", re.I)
STOP_LINE_RE = re.compile(r"www\.dgcmef|^\s*\d{1,3}\s*$|quotidien\s+n", re.I)

CORE_FIELDS = ["autorite_emettrice", "objet", "type_avis", "numero_reference",
               "financement", "date_limite_depot"]


def norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def is_emetteur(line: str) -> bool:
    """Un émetteur d'avis est écrit majoritairement en MAJUSCULES
    (ministère, société, commune…)."""
    l = line.strip()
    if len(l) < 5 or len(l) > 90:
        return False
    letters = [c for c in l if c.isalpha()]
    if not letters:
        return False
    up = sum(1 for c in letters if c.upper() == c)
    return up / len(letters) > 0.75


def detect_type(line: str) -> Optional[str]:
    """Retourne le type d'avis normalisé (Title Case) si la ligne en est un
    en-tête, sinon None."""
    m = TYPE_HEAD_RE.match(line.strip())
    return norm(m.group(1)).title() if m else None


def trim_reference(raw: str) -> Optional[str]:
    """Nettoie une référence 'n° ...' en coupant avant le champ suivant
    (Financement, Montant, Maître d'ouvrage…) qui colle souvent derrière."""
    r = norm(raw)
    if not r:
        return None
    st = REF_STOP_RE.search(r)
    core = (r[:st.start()] if st else r).strip(" .-")
    return ("N° " + core) if core else None


def split_financement(raw: str) -> Optional[str]:
    f = norm(raw)
    if not f:
        return None
    st = FIN_STOP_RE.search(f)
    return (f[:st.start()] if st else f)[:160]


def find_depot(body: str) -> Optional[str]:
    for rgx in DEPOT_RES:
        m = rgx.search(body)
        if m:
            return norm(m.group(0))[:120]
    return None


def parse_montant(raw: str):
    """Classe le montant prévisionnel en {htva, ttc} quand les deux valeurs sont
    présentes (« … HTVA soit … TTC »), sinon {brut}. Coupe le « Cet … » parasite."""
    blob = _MONT_STOP_RE.split(norm(raw))[0].strip() if raw else ""
    if not blob:
        return None
    htva = ttc = None
    mh = _MONT_HTVA_RE.search(blob)
    if mh:
        htva = norm(mh.group(1))
    mt = _MONT_TTC_RE.search(blob)
    if mt:
        ttc = norm(mt.group(1))
    out = {k: v for k, v in (("htva", htva), ("ttc", ttc)) if v}
    return out or {"brut": blob[:160]}


def _lines_with_pages(pdf) -> list:
    out = []
    for i, pg in enumerate(pdf.pages):
        for ln in (pg.extract_text() or "").split("\n"):
            out.append((i + 1, ln.rstrip()))
    return out


def _header_fields(lines: list, idx: int):
    """Remonte depuis l'en-tête de type pour retrouver objet / émetteur / catégorie."""
    objet = emetteur = categorie = None
    back = []
    for j in range(idx - 1, max(idx - 8, -1), -1):
        t = lines[j][1].strip()
        if not t or STOP_LINE_RE.search(t):
            continue
        back.append(t)
        if CAT_RE.match(t):
            categorie = norm(t)
            break
    back.reverse()
    for t in back:
        if CAT_RE.match(t):
            continue
        if emetteur is None and is_emetteur(t):
            emetteur = norm(t)
        elif emetteur is not None:
            objet = norm((objet + " " + t) if objet else t)
    if objet is None and back:
        objet = norm(back[-1])
    return emetteur, objet, categorie


def extract_avis_from_pdf(path: str, numero: Optional[str] = None,
                          label: Optional[str] = None) -> list:
    """Extrait la liste des AVIS d'un PDF de Quotidien. Chaque avis est un dict
    aux clés du schéma AVIS + provenance (_numero, _source_pdf, _source, _page_debut)."""
    import pdfplumber  # import local : dépendance optionnelle, isolée du reste du projet

    if numero is None or label is None:
        name = Path(path).stem.replace("_", " ")
        m = re.search(r"quotidien\s*n?[°o]?\s*(\d{3,4})\s*(bis)?", name, re.I)
        core = (m.group(1) + ("bis" if m.group(2) else "")) if m else Path(path).stem
        numero = numero or ("N" + core)
        label = label or ("Quotidien N°" + core)

    with pdfplumber.open(path) as pdf:
        lines = _lines_with_pages(pdf)

    anchors = []
    for idx, (pg, ln) in enumerate(lines):
        s = ln.strip()
        mh = TYPE_HEAD_RE.match(s)
        if not mh:
            continue
        if len(s) > 85 and not re.search(r"\bn[°o]\b", s):
            continue  # ligne trop longue sans n° : probablement une phrase, pas un en-tête
        window = " ".join(x[1] for x in lines[idx: idx + 10])
        if FIN_RE.search(window) or "participation" in window.lower() or REF_RE.search(window):
            anchors.append((idx, norm(mh.group(1)).title()))

    avis = []
    for a_i, (idx, type_phrase) in enumerate(anchors):
        pg = lines[idx][0]
        end = anchors[a_i + 1][0] if a_i + 1 < len(anchors) else min(idx + 120, len(lines))
        emetteur, objet, categorie = _header_fields(lines, idx)
        body = " ".join(x[1] for x in lines[idx: end])

        ref = REF_RE.search(" ".join(x[1] for x in lines[idx: idx + 7]))
        mont = MONTANT_RE.search(body)
        delai = DELAI_RE.search(body)
        ac = AC_RE.search(body)
        fin = FIN_RE.search(body)

        mont_val = parse_montant(mont.group(1)) if mont else None

        avis.append({
            "autorite_emettrice": emetteur or (norm(ac.group(1))[:160] if ac else None),
            "categorie": categorie,
            "objet": objet[:220] if objet else None,
            "type_avis": type_phrase,
            "numero_reference": trim_reference(ref.group(1)) if ref else None,
            "financement": split_financement(fin.group(1)) if fin else None,
            "montant_previsionnel_global": mont_val,
            "delai_execution": norm(delai.group(1))[:120] if delai else None,
            "date_limite_depot": find_depot(body),
            "flat_text": norm(body)[:2000],  # contexte pour le modèle de décision / l'API
            "_page_debut": pg, "_page_fin": pg,
            "_numero": numero, "_source_pdf": label, "_source": Path(path).name,
        })
    return avis


def enrich_coverage(avis: dict) -> dict:
    """Ajoute un indicateur de complétude de la 1re passe (pas un score
    d'exactitude LLM) + le détail des champs clés manquants, signalés comme
    'à vérifier'. Reste honnête : vide = à contrôler, pas inventé."""
    found = [k for k in CORE_FIELDS if avis.get(k)]
    manquants = [k for k in CORE_FIELDS if not avis.get(k)]
    avis["_score_exactitude"] = round(100 * len(found) / len(CORE_FIELDS))
    avis["_champs_absents"] = []
    avis["_champs_a_verifier"] = manquants
    avis["_mode"] = "deterministe"
    avis["_analyse"] = [
        {"champ": k, "statut": "complete", "confiance": None,
         "justification": "extrait par regex (1re passe, sans révision LLM)"}
        for k in found
    ]
    return avis


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python pdf_avis_extract.py <pdf> [<pdf> ...] [-o sortie.json]")
        sys.exit(1)
    args = sys.argv[1:]
    out_path = Path("dataset_avis.json")
    if "-o" in args:
        i = args.index("-o")
        out_path = Path(args[i + 1])
        args = args[:i] + args[i + 2:]

    all_avis = []
    for p in args:
        av = [enrich_coverage(a) for a in extract_avis_from_pdf(p)]
        all_avis.extend(av)
        print(f"{Path(p).name:40s} -> {len(av):3d} avis")
    out_path.write_text(json.dumps({"AVIS": all_avis}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nTotal : {len(all_avis)} avis -> {out_path}")
