"""
Layer 1 v3 — Extraction directe depuis un fichier Markdown

Ce script parse un fichier .md (généré par OCR/MinerU) et produit
un flux linéaire de blocs unifiés (titres, textes, tableaux),
compatible avec layer2_segment.py.
"""

import re
import json
from pathlib import Path

FOOTER_BREAK_RE = re.compile(
    r"^N°\s*\d{3,5}\s*[–-].*\d{4}\s*$", re.MULTILINE
)
WEBSITE_NOISE_RE = re.compile(r"^www\.[\w.]+\s*$", re.IGNORECASE)


def parse_markdown(md_text: str) -> list[dict]:
    lines = md_text.splitlines()
    stream = []
    
    in_table = False
    current_table = []
    current_text = []

    def flush_text():
        if current_text:
            text = " ".join(current_text).strip()
            if text and not WEBSITE_NOISE_RE.match(text) and not FOOTER_BREAK_RE.search(text) and text != "<!-- image -->" and not re.match(r"^\d+$", text.strip()):
                # On écarte aussi la ligne "Sommaire" qui ne nous sert pas au niveau métier
                if not re.match(r"^Sommaire$", text, re.IGNORECASE):
                    stream.append({"type": "text", "text": text})
            current_text.clear()

    def flush_table():
        if current_table:
            matrix = []
            for row in current_table:
                # Ignorer les lignes de séparation Markdown (ex: |---|---|)
                clean_row = row.strip().strip('|')
                if re.match(r"^[\s\-:]+$", clean_row.replace('|', '')):
                    continue
                
                parts = [c.strip() for c in clean_row.split('|')]
                matrix.append(parts)
            
            flat_text = " | ".join(" ".join(row) for row in matrix)
            if matrix:
                stream.append({
                    "type": "table",
                    "matrix": matrix,
                    "text": flat_text
                })
            current_table.clear()

    for line in lines:
        stripped = line.strip()
        
        # Est-ce une ligne de tableau ?
        if stripped.startswith("|"):
            flush_text()
            current_table.append(stripped)
            in_table = True
            continue
        
        # Fin de tableau ?
        if in_table:
            flush_table()
            in_table = False
            
        # Titre (ex: ## RESULTATS)
        if stripped.startswith("#"):
            flush_text()
            level = 0
            while level < len(stripped) and stripped[level] == '#':
                level += 1
            text = stripped[level:].strip()
            
            # On écarte "Sommaire" pour rester consistant avec l'ancien comportement
            if text and not re.match(r"^Sommaire$", text, re.IGNORECASE):
                stream.append({"type": "title", "level": level, "text": text})
            continue
            
        # Ligne vide -> fin de paragraphe
        if not stripped:
            flush_text()
            continue
            
        # Ligne de texte normal
        current_text.append(stripped)

    flush_text()
    if in_table:
        flush_table()

    # On regroupe tout dans une unique page virtuelle (page 1) 
    # car layer2 supporte la continuité inter-pages de toute façon.
    pages_out = [{
        "page_idx": 0,
        "page_number": 1,
        "stream": stream
    }]
    
    return pages_out


def extract_document_from_markdown(md_path: str) -> list[dict]:
    md_text = Path(md_path).read_text(encoding="utf-8")
    return parse_markdown(md_text)


if __name__ == "__main__":
    import sys
    base_dir = Path(__file__).parent
    md_files = list(base_dir.glob("quotidien_*.md"))
    default_src = str(md_files[0]) if md_files else str(base_dir / "quotidien_4466.md")
    src = sys.argv[1] if len(sys.argv) > 1 else default_src
    
    print(f"Extraction depuis {src}...")
    pages = extract_document_from_markdown(src)
    
    out_path = base_dir / "layer1_output.json"
    out_path.write_text(json.dumps(pages, ensure_ascii=False, indent=2), encoding="utf-8")

    total_blocks = sum(len(p["stream"]) for p in pages)
    total_tables = sum(1 for p in pages for b in p["stream"] if b["type"] == "table")
    total_titles = sum(1 for p in pages for b in p["stream"] if b["type"] == "title")
    print(f"{len(pages)} page virtuelle | {total_blocks} blocs | {total_tables} tableaux | {total_titles} titres")
    print(f"-> {out_path}")