import pymupdf
import sys
import re
import os
from pathlib import Path

def clean_filename(name):
    """Enlève les caractères invalides pour les noms de fichiers sous Windows."""
    # Remplacer les espaces et caractères spéciaux par des underscores
    name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    name = re.sub(r'\s+', '_', name)
    return name

def extract_toc(pdf_path):
    """Extrait le texte de la page 2 et parse les sections et numéros de page."""
    doc = pymupdf.open(pdf_path)
    if len(doc) < 2:
        print("Le PDF n'a pas assez de pages.")
        return [], doc
        
    page_text = doc[1].get_text()
    
    # Extraction du numéro de revue (ex: N° 3931)
    match_num = re.search(r'N°\s*(\d+)', page_text, re.IGNORECASE)
    if not match_num:
        match_num = re.search(r'Num[ée]ro\s*(\d+)', page_text, re.IGNORECASE)
    numero_revue = match_num.group(1) if match_num else "xx"
    
    lines = [line.strip() for line in page_text.split('\n') if line.strip()]
    
    sections = []
    current_context = "UNKNOWN"
    current_categorie = "UNKNOWN"
    current_name_parts = []
    
    for line in lines:
        # Détection du contexte principal
        if "RESULTATS" in line.upper() or "DÉPOUILLEMENTS" in line.upper() or "DEPOUILLEMENTS" in line.upper():
            current_context = "Résultats"
        elif "AVIS" in line.upper():
            current_context = "Avis"
            
        # Détection de la catégorie (persiste jusqu'au prochain changement)
        if re.search(r'minist[èe]res|institutions|mimod|ma[îi]trises', line, re.IGNORECASE):
            current_categorie = "MIMOD"
        elif re.search(r'r[ée]gion', line, re.IGNORECASE):
            current_categorie = "Region"
            
        # Cherche le motif "P. 12" n'importe où (en évitant "B.P.")
        match = re.search(r'(.*?)\.?\b(?<!B\.)P\.\s*(\d+)', line, re.IGNORECASE)
        
        if match:
            text_part = match.group(1).strip()
            # Enlever les points de suite (........) à la fin
            text_part = re.sub(r'\.+$', '', text_part).strip()
            page_num = int(match.group(2))
            
            if text_part:
                current_name_parts.append(text_part)
                
            # Joindre les lignes accumulées
            full_name = " ".join(current_name_parts)
            
            # Détection Type de marché
            type_marche = ""
            if re.search(r'fourniture', full_name, re.IGNORECASE):
                type_marche = "fournitures et services courants"
            elif re.search(r'travaux', full_name, re.IGNORECASE):
                type_marche = "travaux"
            elif re.search(r'intellectuelle', full_name, re.IGNORECASE):
                type_marche = "prestations intellectuelles"
                
            # Assemblage du nom de fichier
            if current_categorie != "UNKNOWN" and type_marche and current_context == "Avis":
                final_name = f"N°{numero_revue}-{current_context}-{current_categorie}-{type_marche}"
            elif current_categorie != "UNKNOWN" and current_context == "Résultats":
                final_name = f"N°{numero_revue}-{current_context}-{current_categorie}"
            else:
                # Fallback si ce n'est pas un marché standard (ex: divers)
                clean_raw = re.sub(r"^[-•*:]\s*", "", full_name).strip()[:50]
                final_name = f"N°{numero_revue}-{current_context}-{clean_raw}"
            
            sections.append({
                "name": clean_filename(final_name),
                "page": page_num
            })
            
            # Réinitialiser pour la section suivante
            current_name_parts = []
        else:
            # Pas de numéro de page -> c'est un morceau de titre qui continue
            clean_line = line.strip()
            if clean_line:
                current_name_parts.append(clean_line)
            
    return sections, doc

def split_pdf(pdf_path):
    sections, doc = extract_toc(pdf_path)
    if not sections:
        print("Aucune section trouvée dans le sommaire.")
        return
        
    print(f"Sommaire extrait ({len(sections)} sections) :")
    for sec in sections:
        print(f" - {sec['name']} (Page imprimée : {sec['page']})")
        
    # Calcul des indices de pages (index 0 = page 1)
    # Souvent, la page imprimée "3" est la 3ème page du fichier (index 2).
    for sec in sections:
        sec['start_index'] = max(0, sec['page'] - 1)
        
    # Trier par index de début pour être sûr de l'ordre
    sections.sort(key=lambda x: x['start_index'])
    
    # Déduire l'index de fin pour chaque section
    for i in range(len(sections)):
        if i < len(sections) - 1:
            sections[i]['end_index'] = sections[i+1]['start_index'] - 1
        else:
            sections[i]['end_index'] = len(doc) - 1
            
    # Préparer le dossier de sortie
    base_dir = Path(__file__).parent
    out_dir = base_dir / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\nDécoupage en cours (sauvegarde dans {out_dir})...")
    for sec in sections:
        start = sec['start_index']
        end = sec['end_index']
        
        # Sécurité si un décalage cause une anomalie
        if start > end:
            print(f"Avertissement: L'intervalle pour {sec['name']} est invalide (Index {start} à {end}). Ignoré.")
            continue
            
        out_name = out_dir / f"{sec['name']}.pdf"
        
        # Création d'un PDF vierge et insertion des pages cibles
        new_doc = pymupdf.open()
        new_doc.insert_pdf(doc, from_page=start, to_page=end)
        new_doc.save(str(out_name))
        new_doc.close()
        
        print(f"Créé : {out_name.name} (Pages d'origine PDF: {start+1} à {end+1})")
        
    doc.close()
    print("\nTerminé avec succès !")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python split_pdf.py <chemin_vers_le_pdf>")
    else:
        split_pdf(sys.argv[1])
