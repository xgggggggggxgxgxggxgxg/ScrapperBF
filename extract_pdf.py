"""
Extraction OCR de x.pdf via PyMuPDF + tesseract (subprocess).
Produit un fichier texte.
"""

import subprocess
import tempfile
import pymupdf
from pathlib import Path

BASE_DIR = Path(__file__).parent
INPUT_PDF = BASE_DIR / "x.pdf"
OUTPUT_TXT = BASE_DIR / "x_ocr.txt"
TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def ocr_pdf(input_path: Path, lang: str = "fra+eng") -> str:
    """OCR chaque page du PDF et retourne le texte complet."""
    doc = pymupdf.open(str(input_path))
    pages_text = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, page in enumerate(doc):
            # Rendu de la page en image PNG
            img_path = Path(tmpdir) / f"page_{i}.png"
            pix = page.get_pixmap(dpi=300)
            pix.save(str(img_path))

            # OCR via tesseract
            out_path = Path(tmpdir) / f"page_{i}"
            subprocess.run(
                [TESSERACT, str(img_path), str(out_path), "-l", lang],
                check=True, capture_output=True,
            )
            txt_path = out_path.with_suffix(".txt")
            text = txt_path.read_text(encoding="utf-8").strip()
            pages_text.append(f"--- Page {i + 1} ---\n{text}")
            print(f"  [OCR] Page {i + 1}/{len(doc)} ({len(text)} caractères)")

    doc.close()
    return "\n\n".join(pages_text)


def main():
    if not INPUT_PDF.exists():
        print(f"[ERREUR] Fichier introuvable: {INPUT_PDF}")
        return

    print(f"[OCR] Début de l'OCR de {INPUT_PDF.name}...")
    full_text = ocr_pdf(INPUT_PDF)

    OUTPUT_TXT.write_text(full_text, encoding="utf-8")
    print(f"[OK] Texte extrait -> {OUTPUT_TXT}")
    print(f"[INFO] {len(full_text)} caractères, {full_text.count(chr(10))} lignes")


if __name__ == "__main__":
    main()
