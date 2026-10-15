"""Détection de mise en page multi-colonnes d'une page PDF.

Utile en amont de la conversion en Markdown : une page à deux colonnes mal
découpée entremêle deux annonces, ce qui produit ensuite des extractions
mélangées impossibles à rattraper en aval.
"""

import argparse

import pymupdf


def check_pdf_has_multiple_columns(pdf_path: str, page_number: int = 0) -> bool:
    doc = pymupdf.open(pdf_path)
    page = doc[page_number]
    
    # Récupérer tous les blocs de texte avec coordonnées [x0, y0, x1, y1]
    blocks = page.get_text("blocks")
    page_mid_x = page.rect.width / 2

    left_blocks = 0
    right_blocks = 0

    for b in blocks:
        x0, y0, x1, y1, text, block_no, block_type = b
        if block_type == 0 and len(text.strip()) > 30: # Blocs de texte significatifs
            if x1 <= page_mid_x + 20:
                left_blocks += 1
            elif x0 >= page_mid_x - 20:
                right_blocks += 1

    # S'il y a un nombre significatif de blocs à gauche ET à droite, la page est multi-colonnes
    return left_blocks >= 2 and right_blocks >= 2

if __name__ == "__main__":
    # Le script exécutait cet appel à l'import, sur un chemin Windows en dur :
    # tout import du module échouait.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--page", type=int, default=0, help="Index de page, 0-based")
    args = parser.parse_args()
    multi = check_pdf_has_multiple_columns(args.pdf, args.page)
    print(f"page {args.page} : {'multi-colonnes' if multi else 'une seule colonne'}")