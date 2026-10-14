import sys
import pymupdf4llm
from pathlib import Path

def convert_all_pdfs_in_dir(directory_path: str):
    """
    Convertit tous les fichiers PDF d'un dossier en texte Markdown.
    """
    dir_path = Path(directory_path)
    if not dir_path.is_dir():
        print(f"Erreur : Le dossier {directory_path} n'existe pas.")
        sys.exit(1)
        
    pdf_files = list(dir_path.glob("*.pdf"))
    print(f"{len(pdf_files)} fichiers PDF trouvés dans {dir_path.name}.")
    
    for input_path in pdf_files:
        print(f"Extraction du markdown depuis : {input_path.name}...")
        try:
            # Conversion du PDF en Markdown structuré
            md_text = pymupdf4llm.to_markdown(str(input_path))
            
            # Sauvegarde
            output_path = input_path.with_suffix('.md')
            output_path.write_text(md_text, encoding="utf-8")
            
            print(f"✅ Sauvegardé : {output_path.name}")
        except Exception as e:
            print(f"❌ Erreur lors de la conversion de {input_path.name} : {e}")

if __name__ == "__main__":
    # Dossier par défaut (celui que vous avez demandé)
    default_dir = r"C:\Users\ASUS\Downloads\files\new_logic\output"
    
    # Si on passe un dossier en argument on l'utilise, sinon on prend celui par défaut
    if len(sys.argv) > 1:
        target_dir = sys.argv[1]
    else:
        target_dir = default_dir
        
    convert_all_pdfs_in_dir(target_dir)
