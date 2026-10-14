import pymupdf
import sys
from pathlib import Path

def extract_single_page(pdf_path, page_num):
    try:
        # Ouvrir le document original
        doc = pymupdf.open(pdf_path)
        
        # Convertir le numéro de page utilisateur (ex: page 1) en index (ex: index 0)
        page_idx = page_num - 1
        
        # Vérifier que la page existe
        if page_idx < 0 or page_idx >= len(doc):
            print(f"Erreur : La page {page_num} n'existe pas. Le document contient {len(doc)} pages.")
            return
            
        # Créer un nouveau document PDF vide
        new_doc = pymupdf.open()
        
        # Insérer uniquement la page désirée
        new_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
        
        # Préparer le chemin du fichier de sortie
        in_path = Path(pdf_path)
        out_name = in_path.parent / f"{in_path.stem}_page_{page_num}.pdf"
        
        # Sauvegarder
        new_doc.save(str(out_name))
        new_doc.close()
        doc.close()
        
        print(f"Succès ! La page {page_num} a été extraite et sauvegardée dans :")
        print(f"-> {out_name}")
        
    except Exception as e:
        print(f"Erreur : {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python extract_single_page.py <chemin_vers_le_pdf> <numero_de_page>")
    else:
        try:
            page_num = int(sys.argv[2])
            extract_single_page(sys.argv[1], page_num)
        except ValueError:
            print("Erreur : Le numéro de page doit être un entier.")
