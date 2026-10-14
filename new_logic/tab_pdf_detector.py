#!/usr/bin/env python3
"""
detect_pdf_font_issues.py

Detecte les pages/polices d'un PDF susceptibles d'etre mal extraites
(texte corrompu du type "8%..$%*+%*34)'9%&.4.'()*") a cause d'un
encodage de police custom sans table ToUnicode correcte.

Usage:
    python3 detect_pdf_font_issues.py mon_fichier.pdf
    python3 detect_pdf_font_issues.py mon_fichier.pdf --pages 52
    python3 detect_pdf_font_issues.py mon_fichier.pdf --ocr-suspects

Sortie:
    - Un rapport texte listant les pages/polices suspectes
    - Un score de "garbling" (0 = propre, 1 = totalement corrompu)
"""

import sys
import argparse
import re
from collections import defaultdict

try:
    import pymupdf  # PyMuPDF
except ImportError:
    print("PyMuPDF n'est pas installe. Lancez: pip install pymupdf --break-system-packages")
    sys.exit(1)


# --- 1. Detection heuristique du texte corrompu -----------------------------

# Lettres frequentes en francais (utilisees pour un score de plausibilite)
FRENCH_COMMON_LETTERS = set("eaisontrludcmpvqgbfhzjxykw")

# Caracteres consideres "anormaux" dans un texte francais normal
SUSPECT_CHARS = set("%*+~^`|<>{}[]\\")



# Patterns a exclure d'office : ce sont des formats numeriques/references
# legitimes qui n'ont pas a "ressembler" a des mots francais.
NUMERIC_LIKE_PATTERNS = [
    re.compile(r"^[\d\s./:()°+\-]+$"),               # que des chiffres/ponctuation (tel, dates, %, page)
    re.compile(r"^[A-Z]{0,6}\d{2,}[A-Z0-9]*\s*$"),     # references/matricules type 00181478N, BFKPL2017B042
    re.compile(r"^P\.?\s*\d+$", re.IGNORECASE),       # "P. 55"
    re.compile(r"^n[°o]\s*[\d/].*", re.IGNORECASE),   # "n°002/2024/SBT/DG/PRM"
    re.compile(r"^\(?\+?\d[\d\s().\-]{5,}$"),          # numeros de telephone avec indicatif
    re.compile(r"^[A-Za-zÀ-ÿ]{1,4}\.?$"),              # sigles courts: "HD.", "TTC.", "G.P.S"
]


def is_numeric_or_reference_like(text: str) -> bool:
    """True si le segment est un format numerique/reference legitime
    (telephone, date, page, sigle, matricule) et ne doit pas etre analyse
    comme du texte en langue naturelle."""
    t = text.strip()
    if not t:
        return True
    for pattern in NUMERIC_LIKE_PATTERNS:
        if pattern.match(t):
            return True
    return False


def garbling_score(text: str) -> float:
    """
    Retourne un score entre 0 (texte propre) et 1 (texte tres probablement
    corrompu par un mauvais encodage de police).

    Les segments qui ressemblent a des numeros/references/sigles legitimes
    (telephones, dates, matricules, "P. 55", "TTC.") sont ignores en amont
    via is_numeric_or_reference_like() et retournent un score de 0.
    """
    text = text.strip()
    if len(text) < 3:
        return 0.0

    if is_numeric_or_reference_like(text):
        return 0.0

    total = len(text)

    # (a) proportion de caracteres suspects (%, *, +, etc. au milieu du texte)
    suspect_count = sum(1 for c in text if c in SUSPECT_CHARS)
    suspect_ratio = suspect_count / total

    # (b) proportion de "mots" (>= 3 lettres) qui ne contiennent aucune
    #     voyelle standard (un vrai mot francais en a presque toujours une).
    #     Les mots courts (sigles, initiales) sont exclus pour eviter les
    #     faux positifs du type "HD", "TTC", "PME".
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ']+", text) if len(w) >= 3]
    if words:
        no_vowel_words = sum(
            1 for w in words if not re.search(r"[aeiouyAEIOUYàâéèêëîïôùûüÀÂÉÈÊËÎÏÔÙÛÜ]", w)
        )
        no_vowel_ratio = no_vowel_words / len(words)
    else:
        no_vowel_ratio = 0.0  # pas assez de mots longs pour juger -> pas de signal

    # (c) proportion de lettres qui ne sont PAS parmi les lettres frequentes
    #     du francais (signal faible mais utile en complement)
    letters = [c.lower() for c in text if c.isalpha()]
    if letters:
        rare_letter_ratio = sum(1 for c in letters if c not in FRENCH_COMMON_LETTERS) / len(letters)
    else:
        rare_letter_ratio = 0.0

    # (d) proportion de caracteres qui NE SONT PAS des lettres alphabetiques,
    #     appliquee uniquement aux segments assez longs pour etre du texte en
    #     langue naturelle (>= 6 caracteres) et qui n'ont pas deja ete
    #     reconnus comme un format numerique/reference legitime. Du vrai texte
    #     francais est majoritairement compose de lettres ; un heading corrompu
    #     par un mauvais encodage de police n'a souvent presque aucune lettre
    #     latine standard, juste des chiffres/symboles.
    if total >= 6:
        alpha_ratio = len(letters) / total
        low_alpha_score = max(0.0, 1.0 - (alpha_ratio / 0.5))  # penalise sous 50% de lettres
        low_alpha_score = min(low_alpha_score, 1.0)
    else:
        low_alpha_score = 0.0

    # Score combine (ponderation empirique, ajustable)
    score = (
        0.25 * suspect_ratio
        + 0.25 * no_vowel_ratio
        + 0.10 * rare_letter_ratio
        + 0.40 * low_alpha_score
    )
    return round(min(score, 1.0), 3)


# --- 2. Inspection des polices / ToUnicode du PDF ----------------------------

def inspect_fonts(doc: "pymupdf.Document"):
    """
    Pour chaque page, liste les polices utilisees et tente de detecter
    l'absence ou l'incompletude d'une table ToUnicode.
    Retourne un dict {page_number: [ (font_name, has_tounicode_flag), ... ]}
    """
    font_report = defaultdict(list)

    for page_index in range(len(doc)):
        page = doc[page_index]
        for font in page.get_fonts(full=True):
            # font tuple: (xref, ext, type, basefont, name, encoding, ...)
            xref = font[0]
            basefont = font[3]
            encoding = font[5] if len(font) > 5 else ""

            font_dict = doc.xref_object(xref, compressed=False)
            has_tounicode = "/ToUnicode" in font_dict

            font_report[page_index + 1].append(
                {
                    "font": basefont,
                    "encoding": encoding,
                    "has_tounicode": has_tounicode,
                }
            )

    return font_report


# --- 3. Analyse complete d'un PDF -------------------------------------------

def analyze_pdf(path: str, target_pages=None, score_threshold: float = 0.35, min_flags_per_page: int = 3):
    doc = pymupdf.open(path)
    font_report = inspect_fonts(doc)

    print(f"\n=== Analyse de {path} ({len(doc)} pages) ===\n")

    flagged_pages = []

    for page_index in range(len(doc)):
        page_num = page_index + 1
        if target_pages and page_num not in target_pages:
            continue

        page = doc[page_index]
        text_dict = page.get_text("dict")

        page_flags = []

        for block in text_dict.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    score = garbling_score(text)
                    if score >= score_threshold:
                        page_flags.append(
                            {
                                "text": text[:60],
                                "score": score,
                                "font": span.get("font", "?"),
                            }
                        )

        if len(page_flags) < min_flags_per_page:
            continue  # trop peu de segments suspects: probablement du bruit / faux positifs isoles

        if page_flags:
            flagged_pages.append(page_num)
            print(f"--- Page {page_num}: {len(page_flags)} segment(s) suspect(s) ---")

            # Recap des polices sans ToUnicode sur cette page
            fonts_no_tounicode = [
                f["font"] for f in font_report.get(page_num, []) if not f["has_tounicode"]
            ]
            if fonts_no_tounicode:
                print(f"  Polices sans table ToUnicode : {sorted(set(fonts_no_tounicode))}")

            for flag in sorted(page_flags, key=lambda x: -x["score"])[:8]:
                print(f"  [score={flag['score']:.2f}] (police: {flag['font']}) \"{flag['text']}\"")
            print()

    if not flagged_pages:
        print("Aucune anomalie detectee avec le seuil actuel.\n")
    else:
        print(f"Resume: {len(flagged_pages)} page(s) suspecte(s) -> {flagged_pages}\n")
        print("Recommandation: pour ces pages, utilisez l'OCR (ex: --ocr-suspects)")
        print("plutot que l'extraction de texte directe.\n")

    doc.close()
    return flagged_pages


# --- 4. OCR de secours pour les pages suspectes ------------------------------

def ocr_pages(path: str, pages: list):
    """
    Rasterise puis OCRise les pages suspectes avec pytesseract (si installe).
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        print("Pour l'OCR, installez: pip install pytesseract pillow --break-system-packages")
        print("et le paquet systeme tesseract-ocr (tesseract-ocr-fra pour le francais).")
        return

    doc = pymupdf.open(path)
    for page_num in pages:
        page = doc[page_num - 1]
        pix = page.get_pixmap(dpi=300)
        img_path = f"/tmp/page_{page_num}.png"
        pix.save(img_path)

        text = pytesseract.image_to_string(Image.open(img_path), lang="fra")
        print(f"\n=== OCR page {page_num} ===\n{text}\n")

    doc.close()


# --- main --------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Detecte les anomalies d'encodage de police dans un PDF")
    parser.add_argument("pdf", help="Chemin vers le fichier PDF")
    parser.add_argument("--pages", type=str, default=None,
                         help="Pages a analyser, ex: 52 ou 1,2,52")
    parser.add_argument("--threshold", type=float, default=0.35,
                         help="Seuil de score de corruption (0-1), defaut 0.35")
    parser.add_argument("--min-flags", type=int, default=3,
                         help="Nb minimum de segments suspects pour flaguer une page, defaut 3")
    parser.add_argument("--ocr-suspects", action="store_true",
                         help="Lance l'OCR sur les pages detectees comme suspectes")

    args = parser.parse_args()

    target_pages = None
    if args.pages:
        target_pages = [int(p.strip()) for p in args.pages.split(",")]

    flagged = analyze_pdf(args.pdf, target_pages=target_pages, score_threshold=args.threshold,
                           min_flags_per_page=args.min_flags)

    if args.ocr_suspects and flagged:
        ocr_pages(args.pdf, flagged)


if __name__ == "__main__":
    main()