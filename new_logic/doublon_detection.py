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

# Exemple :
is_multicolumn = check_pdf_has_multiple_columns("C:\\Users\\ASUS\\Downloads\\t_page_46.pdf")
print(is_multicolumn)