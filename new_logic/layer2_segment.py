"""
Layer 2 — Segmentation en unités logiques (heuristique déterministe, fallback LLM en option)

Deux niveaux de découpage :
  A) Macro-section : RESULTATS vs AVIS (détecté via les titres level=1 du sommaire /
     des en-têtes de section : "RESULTATS PROVISOIRES", "APPELS D'OFFRES", "AVIS...")
  B) Unité (un marché / un avis) à l'intérieur de chaque macro-section.

Règle de découpage d'unité (robuste, sans mots-clés figés) :
  - Un nouveau titre de level=1 ou level=2 démarre potentiellement une nouvelle unité
    UNIQUEMENT si le contenu qui suit ressemble à une nouvelle entité (pas une simple
    sous-partie du même marché). Pour trancher, on utilise un signal fiable et générique :
    la clôture de l'unité précédente.
  - Pour RESULTATS : une unité se termine quand on rencontre une ligne de tableau
    contenant le mot "ATTRIBUTAIRE" (quel que soit l'entourage exact du texte) — c'est
    la ligne de décision finale, toujours présente par construction du document.
    Tout ce qui suit avant le prochain titre de niveau 1/2 appartient à la nouvelle unité.
  - Pour AVIS : pas de marqueur de clôture aussi net -> une nouvelle unité démarre à
    chaque titre de level=1 (un avis = un objet de marché = un titre de premier niveau),
    les level=2 qui suivent (référence, financement...) restent rattachés à cette unité.
  - Continuité inter-pages : comme il n'y a pas de répétition d'en-tête entre deux pages
    pour un même marché encore ouvert, on NE réinitialise PAS l'unité courante au
    changement de page : le flux est traité page après page sans rupture artificielle.

Ce découpage n'a pas besoin de connaître le vocabulaire exact ("SYNTHESE", "DPX", etc.) :
il repose sur la structure (titres/niveaux) et un seul marqueur générique ("ATTRIBUTAIRE").
Si un document futur ne suit pas ce marqueur, la Couche 2b (fallback LLM) prend le relai
pour ce bloc plutôt que de faire planter le pipeline.
"""

import json
import re
from pathlib import Path
from typing import Optional

def markdown_to_layer1_format(md_filepath: str) -> list[dict]:
    """
    Lit un fichier Markdown et le convertit en structure de blocs compatibles Layer 1.
    """
    md_text = Path(md_filepath).read_text(encoding="utf-8")
    lines = md_text.splitlines()
    
    stream = []
    in_table = False
    current_table = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
            
        if stripped.startswith("|") and stripped.endswith("|"):
            in_table = True
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            # Ignorer la ligne séparatrice de markdown (ex: |---|---|)
            if all(set(c) <= set(" -:") for c in cells if c):
                continue
            current_table.append(cells)
        else:
            if in_table:
                stream.append({
                    "type": "table",
                    "text": "\n".join(" | ".join(row) for row in current_table),
                    "matrix": current_table
                })
                in_table = False
                current_table = []
                
            # Nettoyage des balises HTML/Markdown parasites
            clean_stripped = re.sub(r'</?mark>', '', stripped)
            clean_text_no_bold = re.sub(r'\*\*', '', clean_stripped).strip()
            
            if clean_stripped.startswith("#"):
                level = len(clean_stripped) - len(clean_stripped.lstrip("#"))
                text = clean_text_no_bold.lstrip("#").strip()
                stream.append({
                    "type": "title",
                    "level": level,
                    "text": text
                })
            # Si la ligne est un titre de section évident mais n'a pas de '#'
            elif re.search(r'^(RESULTATS\s+PROVISOIRES|APPELS?\s+D\'?OFFRES|AVIS\s+DE|AVIS\s+A\s+MANIFESTATION|PRESTATIONS\s+INTELLECTUELLES)', clean_text_no_bold, re.IGNORECASE):
                stream.append({
                    "type": "title",
                    "level": 1,
                    "text": clean_text_no_bold
                })
            else:
                stream.append({
                    "type": "text",
                    "text": clean_text_no_bold
                })
                
    if in_table:
        stream.append({
            "type": "table",
            "text": "\n".join(" | ".join(row) for row in current_table),
            "matrix": current_table
        })
        
    return [{"page_number": 1, "stream": stream}]

RESULTATS_SECTION_RE = re.compile(r"RESULTATS\s+PROVISOIRES", re.IGNORECASE)
AVIS_SECTION_RE = re.compile(r"APPELS?\s+D'?OFFRES|AVIS\s+DE|AVIS\s+A\s+MANIFESTATION|PRESTATIONS\s+INTELLECTUELLES", re.IGNORECASE)
ATTRIBUTAIRE_RE = re.compile(r"ATTRIBUTAIRE", re.IGNORECASE)
PAGE_NUMBER_RE = re.compile(r'^\d{1,3}$')
TOC_MARKER_RE = re.compile(r"^Sommaire$", re.IGNORECASE)

# Un titre "objet de marché" démarre presque toujours par un nom d'action
# administrative. Ce signal s'avère plus fiable que le champ `level` du parser,
# qui se trompe parfois (ex: un nouvel objet classé level=2 au lieu de level=1
# à cause d'une mise en page particulière). C'est le marqueur générique utilisé
# pour repérer le début d'un NOUVEL avis, indépendamment du niveau hiérarchique.
OBJECT_TITLE_RE = re.compile(
    r"^(Acquisition|Travaux|R[ée]alisation|Fourniture|Entretien|Construction|"
    r"Constitution|R[ée]habilitation|Am[ée]nagement|Approvisionnement|"
    r"Location|Recrutement|Prestation|[ÉE]laboration|[ÉE]tude|Audit|Formation|"
    r"Assistance|Contr[ôo]le|Suivi|S[ée]lection|Consultation|Mission)",
    re.IGNORECASE,
)

# --- Étiquetage catégorie / type des AVIS -----------------------------------
# Deux catégories d'autorité, dans cet ordre dans le document :
#   1) Ministères, institutions et maîtrises d'ouvrages déléguées
#   2) Collectivités territoriales
# Trois types de marché possibles dans chaque catégorie (marqueurs pas toujours
# présents/propres dans le document réel -> on garde aussi un filet de sécurité
# par mots-clés sur l'objet de l'avis lui-même).
CATEGORIE_COLLECTIVITES_RE = re.compile(r"COLLECTIVIT[EÉ]S?\s+TERRITORIALES?", re.IGNORECASE)
CATEGORIE_MINISTERES_RE = re.compile(r"MINISTERES?\s+ET\s+INSTITUTIONS|MAITRISES?\s+D.OUVRAGES?\s+D[ÉE]L[ÉE]GU[ÉE]ES?", re.IGNORECASE)

TYPE_FOURNITURES_RE = re.compile(r"^Fournitures?\s+et\s+Services?\s+courants?$", re.IGNORECASE)
TYPE_TRAVAUX_RE = re.compile(r"^Travaux$", re.IGNORECASE)  # marqueur de type bare, PAS "Travaux de..."
TYPE_PRESTATIONS_RE = re.compile(r"^Prestations?\s+intellectuelles?$", re.IGNORECASE)

# Filet de sécurité : le type se déduit du verbe d'objet quand le marqueur de
# section est absent ou fusionné avec le titre suivant (cas fréquent constaté
# dans le document réel : "## Travaux de construction..." sans "## Travaux" isolé).
TYPE_KEYWORD_TRAVAUX_RE = re.compile(r"^(Travaux|Construction|R[ée]alisation|R[ée]habilitation|Am[ée]nagement)", re.IGNORECASE)
TYPE_KEYWORD_PRESTATIONS_RE = re.compile(r"Prestation|manifestation d.int[ée]r[êe]t|consultant", re.IGNORECASE)


def detect_categorie(title_text: str, current: str) -> str:
    if CATEGORIE_COLLECTIVITES_RE.search(title_text):
        return "COLLECTIVITES"
    if CATEGORIE_MINISTERES_RE.search(title_text):
        return "MINISTERES"
    return current


def detect_type_marker(title_text: str) -> Optional[str]:
    """Retourne le type SEULEMENT si le titre est un marqueur de section bare
    (pas un objet de marché qui contiendrait accidentellement le mot)."""
    t = title_text.strip()
    if TYPE_FOURNITURES_RE.match(t):
        return "Fournitures et Services courants"
    if TYPE_TRAVAUX_RE.match(t):
        return "Travaux"
    if TYPE_PRESTATIONS_RE.match(t):
        return "Prestations intellectuelles"
    return None


def infer_type_from_object(object_title: str) -> Optional[str]:
    """Détection par mot-clé sur l'objet lui-même. Retourne None si ambigu
    (laisse alors la place au dernier marqueur de type explicite vu, s'il y en
    a un ; sinon la valeur par défaut "Fournitures et Services courants")."""
    if TYPE_KEYWORD_PRESTATIONS_RE.search(object_title):
        return "Prestations intellectuelles"
    if TYPE_KEYWORD_TRAVAUX_RE.match(object_title.strip()):
        return "Travaux"
    return None


def page_is_toc(page: dict) -> bool:
    """Une page 'Sommaire' est structurellement différente (table des matières,
    pas des marchés) : on l'écarte de la segmentation métier plutôt que de la
    forcer dans un pattern qui ne lui correspond pas."""
    for block in page["stream"]:
        if block["type"] == "title" and TOC_MARKER_RE.match(block["text"].strip()):
            return True
    return False


def detect_section(title_text: str, current_section: str) -> str:
    if RESULTATS_SECTION_RE.search(title_text):
        return "RESULTATS"
    if AVIS_SECTION_RE.search(title_text):
        return "AVIS"
    return current_section


def block_has_attributaire(block: dict) -> bool:
    if block["type"] == "table":
        return bool(ATTRIBUTAIRE_RE.search(block["text"]))
    if block["type"] == "text":
        return bool(ATTRIBUTAIRE_RE.search(block["text"]))
    return False


def _new_unit_dict(section: str, page_num: int, categorie=None, type_marche=None) -> dict:
    d = {"section": section, "page_debut": page_num, "page_fin": page_num, "blocks": []}
    if section == "AVIS":
        d["categorie"] = categorie or "MINISTERES"
        d["type_marche"] = type_marche or "Fournitures et Services courants"
    return d


def segment_document(pages: list[dict]) -> dict:
    """
    Retourne {"RESULTATS": [unites...], "AVIS": [unites...], "NON_CLASSE": [...]}
    Chaque unité = {"section": ..., "page_debut": ..., "page_fin": ..., "blocks": [...]}
    """
    sections = {"RESULTATS": [], "AVIS": [], "NON_CLASSE": []}

    current_section = "NON_CLASSE"
    current_unit = None  # dict en cours de construction
    awaiting_new_unit = True  # True juste après une clôture ou un changement de section
    content_seen_in_unit = False  # True dès qu'un bloc text/table a rejoint l'unité en cours
    current_categorie = None   # état suivi en continu sur tout le document
    current_type_marker = None  # dernier marqueur de type EXPLICITE rencontré

    def flush_unit():
        nonlocal current_unit, content_seen_in_unit
        if current_unit and current_unit["blocks"]:
            sections[current_unit["section"]].append(current_unit)
        current_unit = None
        content_seen_in_unit = False

    for page in pages:
        if page_is_toc(page):
            continue  # table des matières : hors périmètre de la segmentation métier

        page_num = page["page_number"]
        for block in page["stream"]:

            if block["type"] == "title":
                new_section = detect_section(block["text"], current_section)
                section_changed = new_section != current_section
                current_section = new_section

                # Étiquetage catégorie / type, mis à jour en continu, indépendamment
                # du découpage en unités (ces marqueurs ne créent jamais eux-mêmes
                # de nouvelle unité, ils informent juste l'état courant).
                new_categorie = detect_categorie(block["text"], current_categorie)
                if new_categorie != current_categorie:
                    current_type_marker = None  # le type ne "survit" pas à un changement de catégorie
                current_categorie = new_categorie
                explicit_type = detect_type_marker(block["text"])
                if explicit_type:
                    current_type_marker = explicit_type

                is_entity_header = block.get("level", 1) <= 2
                is_type_marker = explicit_type is not None

                # RESULTATS : une nouvelle unité démarre après clôture (ATTRIBUTAIRE) ou
                # changement de section -> on respecte awaiting_new_unit.
                # AVIS : un marqueur de type ("Fournitures et Services courants",
                # "Travaux", "Prestations intellectuelles") sépare chaque avis.
                # Chaque occurrence de ce marqueur démarre une nouvelle unité.
                starts_new_unit = section_changed or (
                    (current_section == "RESULTATS" and is_entity_header
                     and block.get("level", 1) == 1 and awaiting_new_unit)
                    or (current_section == "AVIS" and is_type_marker and content_seen_in_unit)
                )

                if starts_new_unit:
                    flush_unit()
                    current_unit = _new_unit_dict(
                        current_section, page_num,
                        current_categorie,
                        infer_type_from_object(block["text"]) or current_type_marker or "Fournitures et Services courants",
                    )
                    awaiting_new_unit = False

                if current_unit is None:
                    current_unit = _new_unit_dict(current_section, page_num, current_categorie, current_type_marker)

                current_unit["blocks"].append(block)
                current_unit["page_fin"] = page_num
                continue

            if block["type"] == "image":
                continue  # pas de contenu exploitable pour la segmentation

            # text / table
            # Détection des marqueurs de type même dans les blocs texte (cas où
            # le MD n'a pas de '#' devant "Fournitures et Services courants")
            if block["type"] == "text" and current_section == "AVIS":
                text_type = detect_type_marker(re.sub(r'</?mark>', '', re.sub(r'\*\*', '', block["text"])).strip())
                if text_type:
                    current_type_marker = text_type
                    if content_seen_in_unit:
                        flush_unit()
                    if current_unit is None:
                        current_unit = _new_unit_dict(current_section, page_num, current_categorie, current_type_marker)
                    current_unit["blocks"].append(block)
                    current_unit["page_fin"] = page_num
                    continue

            if current_unit is None:
                current_unit = _new_unit_dict(current_section, page_num, current_categorie, current_type_marker)

            current_unit["blocks"].append(block)
            current_unit["page_fin"] = page_num
            content_seen_in_unit = True

            if current_section == "RESULTATS" and block_has_attributaire(block):
                # l'unité se termine ici ; la prochaine ligne utile ouvre une nouvelle unité
                flush_unit()
                awaiting_new_unit = True

    flush_unit()

    # Nettoyage : les unités sans contenu substantiel (juste un titre de section/
    # catégorie, ou un fragment de bannière capturé comme texte) sont fusionnées
    # avec l'unité suivante de la même section plutôt que gardées comme avis à part.
    MIN_CONTENT_CHARS = 60
    STRUCTURAL_TEXT_RE = re.compile(
        r"MINISTERES?\s+ET\s+INSTITUTIONS|MAITRISES?\s+D.OUVRAGES?|COLLECTIVIT[EÉ]S?\s+TERRITORIALES?",
        re.IGNORECASE,
    )
    for section, units in sections.items():
        merged = []
        pending_titles = []
        for u in units:
            content_chars = sum(
                len(b["text"]) for b in u["blocks"]
                if b["type"] in ("text", "table") and not STRUCTURAL_TEXT_RE.search(b["text"])
            )
            has_real_content = content_chars >= MIN_CONTENT_CHARS
            if not has_real_content:
                pending_titles.extend(u["blocks"])
                continue
            if pending_titles:
                u["blocks"] = pending_titles + u["blocks"]
                pending_titles = []
            merged.append(u)
        sections[section] = merged

    # Propagation du contexte : une unité sans titre propre hérite du dernier
    # titre d'entité rencontré dans la même section (cas des institutions ayant
    # plusieurs marchés consécutifs sans répéter leur en-tête).
    for sec_units in sections.values():
        last_entity_title = None
        for u in sec_units:
            own_title = next((b["text"] for b in u["blocks"] if b["type"] == "title"), None)
            if own_title:
                last_entity_title = own_title
            else:
                u["entite_heritee"] = last_entity_title

    return sections


def extract_page_numbers(sections: dict):
    """Enrichit chaque unité avec le numéro de page PDF détecté."""
    for units in sections.values():
        for unit in units:
            page_pdf = None
            for block in unit["blocks"]:
                if block["type"] == "text" and PAGE_NUMBER_RE.match(block["text"].strip()):
                    page_pdf = int(block["text"].strip())
            if page_pdf is not None:
                unit["page_debut"] = page_pdf
                unit["page_fin"] = page_pdf


def unit_to_text(unit: dict) -> str:
    """Aplati une unité en texte brut prêt à être envoyé à la Couche 3 (LLM)."""
    parts = []
    if unit.get("entite_heritee"):
        parts.append(f"# {unit['entite_heritee']} (entité héritée du bloc précédent)")
    for b in unit["blocks"]:
        if b["type"] == "title":
            parts.append(f"# {b['text']}")
        elif b["type"] == "text":
            parts.append(b["text"])
        elif b["type"] == "table":
            parts.append("[TABLEAU]")
            for row in b["matrix"]:
                parts.append(" | ".join(row))
    return "\n".join(parts)


if __name__ == "__main__":
    import sys
    base_dir = Path(__file__).parent
    
    in_path_str = sys.argv[1] if len(sys.argv) > 1 else str(base_dir / "layer1_output.json")
    in_path = Path(in_path_str)
    
    if in_path.suffix.lower() == ".md":
        print(f"Chargement du fichier Markdown : {in_path.name}")
        pages = markdown_to_layer1_format(str(in_path))
    else:
        print(f"Chargement du fichier JSON : {in_path.name}")
        pages = json.load(open(in_path, encoding="utf-8"))
        
    sections = segment_document(pages)
    extract_page_numbers(sections)

    print(f"AVIS : {len(sections.get('AVIS', []))} unités")

    # Aperçu de contrôle
    if sections.get("AVIS"):
        print("\n--- Exemple d'unité AVIS (texte aplati) ---")
        print(unit_to_text(sections["AVIS"][0])[:1200])

    out_path = base_dir / "layer2_output.json"
    serializable = {
        sec: [
            {**u, "flat_text": unit_to_text(u)} for u in units
        ]
        for sec, units in sections.items()
    }
    out_path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n-> {out_path}")