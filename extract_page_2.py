import pymupdf
import sys

def extract_page_2(pdf_path):
    try:
        # Open the document
        doc = pymupdf.open(pdf_path)
        
        # Check if the document has at least 2 pages
        if len(doc) < 2:
            print("Le document PDF contient moins de 2 pages.")
            return
            
        # Get the second page (index 1)
        page = doc[1]
        
        # Extract text
        text = page.get_text()
        print(f"--- Contenu de la page 2 ({pdf_path}) ---")
        print(text)
        
    except Exception as e:
        print(f"Erreur lors de l'ouverture du PDF : {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python extract_page_2.py <chemin_vers_le_pdf>")
    else:
        extract_page_2(sys.argv[1])
