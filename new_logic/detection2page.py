#!/usr/bin/env python3
"""
detect_multipage_ads.py

Detecte, dans un texte markdown extrait d'un bulletin de marches publics
(type "Quotidien des marches publics"), les avis qui s'etendent sur
PLUSIEURS pages consecutives (au lieu d'un avis = une page).

Principe:
    1. Le texte est d'abord decoupe en "pages" en reperant les motifs
       recurrents de pied/en-tete de page (nom du bulletin + date + numero
       de page en gras, ex: "**34**").
    2. Pour chaque page, on calcule des indices:
         - demarre-t-elle par un NOUVEL avis (secteur + organisme + titre
           "AVIS A MANIFESTATION D'INTERET" ou equivalent) ?
         - se termine-t-elle par un bloc de signature / formule de cloture ?
         - contient-elle une numerotation (1. 2. 3...) et laquelle ?
         - se termine-t-elle en pleine phrase / ligne de tableau coupee ?
    3. On chaine les pages : une page est consideree comme la CONTINUATION
       de la precedente si elle ne demarre pas par un nouvel avis ET qu'au
       moins un signal de coupure est present (numerotation qui continue,
       pas de signature en fin de page precedente, phrase tronquee, etc.)

Usage:
    python3 detect_multipage_ads.py fichier.md
    python3 detect_multipage_ads.py fichier.md --verbose
"""

import re
import sys
import argparse
from dataclasses import dataclass, field


# --- 1. Decoupage en pages ---------------------------------------------------

# Motif de pied de page recurrent du bulletin, ex:
# "> **_N° 4466 - Vendredi 14 août 2026_** Larevue DGCMEFded s ics"
# "Quotidien d'information de la Direction Générale du Contrôle des ..."
# "**34**"
# On utilise le numero de page en gras isole sur sa ligne comme separateur
# principal (robuste car il apparait systematiquement en bas de chaque page).
PAGE_NUMBER_FOOTER = re.compile(r"^\*\*(\d{1,4})\*\*\s*$", re.MULTILINE)

# Lignes de bandeau repetitives a ignorer lors de l'analyse du contenu
# (elles n'apportent aucun signal utile et polluent le debut/fin de page)
BOILERPLATE_LINE_PATTERNS = [
    re.compile(r"N°\s*\d+\s*-\s*\w+\s+\d{1,2}\s+\w+\s+\d{4}", re.IGNORECASE),
    re.compile(r"Quotidien d.information de la Direction G", re.IGNORECASE),
    re.compile(r"Marchés Publics et des Engagements Financiers", re.IGNORECASE),
    re.compile(r"www\.dgcmef\.gov\.bf", re.IGNORECASE),
    re.compile(r"^>\s*$"),
]


def strip_boilerplate(text: str) -> str:
    lines = text.split("\n")
    kept = []
    for line in lines:
        if any(p.search(line) for p in BOILERPLATE_LINE_PATTERNS):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def split_into_pages(md_text: str):
    """
    Decoupe le texte en pages en utilisant le numero de page en gras
    (ex: "**34**") comme marqueur de fin de page.
    Retourne une liste de dicts {page_number, text}.
    """
    matches = list(PAGE_NUMBER_FOOTER.finditer(md_text))

    if not matches:
        # Pas de marqueur de page detecte : on traite tout comme une seule page
        return [{"page_number": None, "text": strip_boilerplate(md_text)}]

    pages = []
    start = 0
    for m in matches:
        chunk = md_text[start:m.start()]
        page_number = int(m.group(1))
        pages.append({"page_number": page_number, "text": strip_boilerplate(chunk)})
        start = m.end()

    # Reste apres le dernier marqueur (rare, mais on le rattache a la derniere page)
    trailing = md_text[start:].strip()
    if trailing and pages:
        pages[-1]["text"] += "\n" + strip_boilerplate(trailing)

    return pages


# --- 2. Signaux par page ------------------------------------------------------

# Debut d'un NOUVEL avis : secteur en gras/majuscules suivi (de pres) d'un
# organisme en majuscules et/ou d'un intitule "AVIS A ..." / "DEMANDE DE...".
NEW_NOTICE_SECTOR_PATTERN = re.compile(
    r"(Prestations intellectuelles|Travaux|Fournitures|Services courants)",
    re.IGNORECASE,
)
NEW_NOTICE_TITLE_PATTERN = re.compile(
    r"AVIS\s+(A|D['’])\s*(MANIFESTATION|APPEL|CONSULTATION)|DEMANDE DE PROPOSITIONS|"
    r"APPEL D['’]OFFRES",
    re.IGNORECASE,
)

# Formules de cloture typiques en fin d'avis
SIGNATURE_PATTERNS = re.compile(
    r"(Président de la Commission|Le Directeur Général|Le Président|"
    r"Personne Responsable des Marchés|Fait à |PRCP\)?\s*$|Chevalier de l.ordre)",
    re.IGNORECASE,
)

# Elements numerotes en debut de ligne : "1.", "2)", etc.
NUMBERED_ITEM = re.compile(r"^\s*(\d{1,2})[.\)]\s", re.MULTILINE)


@dataclass
class PageSignals:
    page_number: int
    starts_with_new_notice: bool
    ends_with_signature: bool
    numbered_items: list = field(default_factory=list)
    ends_truncated: bool = False
    text_preview_start: str = ""
    text_preview_end: str = ""


def analyze_page(page_number, text: str) -> PageSignals:
    text = text.strip()

    # Ne regarder que le tout debut du texte pour statuer sur "nouvel avis"
    head = text[:400]
    starts_new = bool(NEW_NOTICE_SECTOR_PATTERN.search(head)) or bool(
        NEW_NOTICE_TITLE_PATTERN.search(head)
    )

    tail = text[-400:]
    ends_signature = bool(SIGNATURE_PATTERNS.search(tail))

    numbered = [int(n) for n in NUMBERED_ITEM.findall(text)]

    # Phrase tronquee : la page ne se termine ni par une ponctuation forte
    # (. ! ? :) ni par un element de mise en forme de fin (tableau, tiret)
    stripped_tail = text.rstrip()
    ends_truncated = bool(stripped_tail) and stripped_tail[-1] not in ".!?:»”\")"

    return PageSignals(
        page_number=page_number,
        starts_with_new_notice=starts_new,
        ends_with_signature=ends_signature,
        numbered_items=numbered,
        ends_truncated=ends_truncated,
        text_preview_start=head[:120].replace("\n", " "),
        text_preview_end=tail[-120:].replace("\n", " "),
    )


# --- 3. Chainage des pages en avis --------------------------------------------

def group_pages_into_notices(pages_signals: list, verbose: bool = False):
    """
    Regroupe les pages consecutives appartenant au meme avis.
    Retourne une liste de groupes, chaque groupe etant une liste de
    page_number, avec la raison de continuation pour les pages fusionnees.
    """
    groups = []
    current_group = []
    reasons = []

    for i, sig in enumerate(pages_signals):
        if i == 0 or sig.starts_with_new_notice:
            # Nouvelle page = nouvel avis (ou toute premiere page du document)
            if current_group:
                groups.append({"pages": current_group, "reasons": reasons})
            current_group = [sig.page_number]
            reasons = []
        else:
            prev = pages_signals[i - 1]
            continuation_signals = []

            if not prev.ends_with_signature:
                continuation_signals.append("page précédente sans bloc de signature")

            if prev.ends_truncated:
                continuation_signals.append("page précédente coupée en pleine phrase/ligne")

            if prev.numbered_items and sig.numbered_items:
                prev_max = max(prev.numbered_items)
                sig_min = min(sig.numbered_items)
                if sig_min == prev_max + 1:
                    continuation_signals.append(
                        f"numérotation continue ({prev_max} -> {sig_min})"
                    )

            if continuation_signals:
                current_group.append(sig.page_number)
                reasons.append({"page": sig.page_number, "signals": continuation_signals})
            else:
                # Pas de titre detecte, mais pas non plus de signal net de
                # continuation -> on la traite prudemment comme un nouvel
                # avis (mieux vaut sous-fusionner que sur-fusionner), tout
                # en le signalant comme incertain.
                if current_group:
                    groups.append({"pages": current_group, "reasons": reasons})
                current_group = [sig.page_number]
                reasons = [{"page": sig.page_number, "signals": ["INCERTAIN: pas de titre détecté mais pas de signal de continuation net non plus"]}]

    if current_group:
        groups.append({"pages": current_group, "reasons": reasons})

    return groups


# --- main ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Detecte les avis multi-pages dans un bulletin markdown")
    parser.add_argument("file", help="Fichier markdown a analyser")
    parser.add_argument("--verbose", action="store_true", help="Affiche le detail des signaux par page")
    args = parser.parse_args()

    with open(args.file, "r", encoding="utf-8") as f:
        md_text = f.read()

    pages = split_into_pages(md_text)
    signals = [analyze_page(p["page_number"], p["text"]) for p in pages]

    if args.verbose:
        print("=== Signaux par page ===\n")
        for s in signals:
            print(f"Page {s.page_number}:")
            print(f"  nouvel avis en debut de page : {s.starts_with_new_notice}")
            print(f"  signature en fin de page      : {s.ends_with_signature}")
            print(f"  items numerotes               : {s.numbered_items}")
            print(f"  coupure en fin de page         : {s.ends_truncated}")
            print(f"  debut : \"{s.text_preview_start}...\"")
            print(f"  fin   : \"...{s.text_preview_end}\"")
            print()

    groups = group_pages_into_notices(signals, verbose=args.verbose)

    print("=== Avis detectes ===\n")
    for g in groups:
        pages_str = ", ".join(str(p) for p in g["pages"])
        span = "page unique" if len(g["pages"]) == 1 else f"{len(g['pages'])} pages"
        print(f"Avis pages [{pages_str}]  ({span})")
        for r in g["reasons"]:
            print(f"    -> page {r['page']} rattachee car: {', '.join(r['signals'])}")
        print()

    multipage = [g for g in groups if len(g["pages"]) > 1]
    print(f"Resume: {len(groups)} avis detecte(s), dont {len(multipage)} sur plusieurs pages.")


if __name__ == "__main__":
    main()