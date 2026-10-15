"""
Structuration LLM d'une unité-émetteur de la section RÉSULTATS.

Entrée : une unité produite par pdf_resultats_extract.segment_resultats()
         (un émetteur = tout ce qui est entre deux « cadres noirs »), qui contient
         un ou plusieurs lots et un tableau multi-colonnes complexe.

Problème (posé par l'utilisateur) : ces tableaux ont des colonnes imbriquées
  Soumissionnaire | Montant lu (HTVA min/max, TTC min/max) | Montant corrigé
  (HTVA min/max, TTC min/max) | Observations
qui s'étalent sur plusieurs pages et dont les cellules sont multi-lignes.
L'extraction texte brute les aplatit ; il faut un LLM pour les restructurer.

Ce module fournit LES INSTRUCTIONS à l'IA (prompt système détaillé + schéma de
sortie) et l'appel via la rotation de providers de la Couche 3. On AIDE le modèle
avec un indice déterministe : le nombre de bandes « Lot » repérées dans la page
(unit["nb_lots_detectes"]) — pour qu'il sache combien de sous-tableaux attendre.

Pas de réseau ici : testé hors-ligne avec des providers injectés.
"""

import json
from typing import Optional

from layer3_extract_llm import call_with_rotation


# Schéma de sortie : un émetteur -> plusieurs lots -> plusieurs soumissionnaires.
def _montant_pair(desc):
    return {"type": "object", "properties": {
        "min": {"type": "string", "description": desc + " (minimum, tel qu'écrit)"},
        "max": {"type": "string", "description": desc + " (maximum, tel qu'écrit)"},
    }}


RESULTATS_SCHEMA = {
    "type": "object",
    "properties": {
        "autorite_contractante": {"type": "string", "description": "Nom de l'émetteur (barre noire)"},
        "reference": {"type": "string", "description": "N° du dossier / de l'appel, tel qu'écrit"},
        "objet": {"type": "string", "description": "Objet global du marché"},
        "financement": {"type": "string"},
        "date_deliberation": {"type": "string", "description": "Date de délibération de la commission"},
        "nombre_lots": {"type": "integer", "description": "Nombre EXACT de lots dans ce tableau"},
        "lots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "numero": {"type": "integer"},
                    "intitule": {"type": "string", "description": "Libellé du lot (bande orange « Lot N : … »)"},
                    "soumissionnaires": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "nom": {"type": "string"},
                                "montant_lu_htva": _montant_pair("Montant lu HTVA"),
                                "montant_corrige_htva": _montant_pair("Montant corrigé HTVA"),
                                "montant_lu_ttc": _montant_pair("Montant lu TTC"),
                                "montant_corrige_ttc": _montant_pair("Montant corrigé TTC"),
                                "conformite": {"type": "string", "description": "conforme | non conforme | non recevable | …"},
                                "observations": {"type": "string", "description": "Motif/observation, recopié"},
                            },
                        },
                    },
                    "attributaire_provisoire": {
                        "type": "object",
                        "properties": {
                            "nom": {"type": "string"},
                            "montant_min_ttc": {"type": "string"},
                            "montant_max_ttc": {"type": "string"},
                            "delai_execution": {"type": "string"},
                        },
                    },
                    "infructueux": {"type": "boolean", "description": "true si le lot est déclaré infructueux"},
                },
            },
        },
    },
    "required": ["autorite_contractante", "nombre_lots", "lots"],
}


RESULTATS_SYSTEM_PROMPT = """Tu es un expert de la lecture des tableaux de RÉSULTATS provisoires des marchés
publics du Burkina Faso (Quotidien DGCMEF). On te donne le texte APLATI d'un seul
émetteur (tout ce qui se trouve entre deux « cadres noirs »). Ce texte provient
d'un ou plusieurs tableaux multi-colonnes qui se lisent mal une fois mis à plat.

STRUCTURE À RECONSTRUIRE (impérative) :
1. Un émetteur peut contenir PLUSIEURS LOTS. Chaque lot est introduit par une
   bande « Lot N : <intitulé> ». S'il n'y a aucune bande « Lot », c'est qu'il y a
   un lot unique (non numéroté) : rends alors un seul lot.
   -> Donne le nombre EXACT de lots dans "nombre_lots".
2. Pour CHAQUE lot, le tableau liste les SOUMISSIONNAIRES. Les colonnes sont :
     - Soumissionnaire (nom de l'entreprise/groupement)
     - « Montant lu » : décomposé en HTVA (Minimum/Maximum) ET TTC (Minimum/Maximum)
     - « Montant corrigé » : décomposé en HTVA (Minimum/Maximum) ET TTC (Minimum/Maximum)
     - Observations (conformité + motif)
   Beaucoup de cellules valent « -- » (non renseigné) : recopie-les en null.
   Les nombres sont parfois coupés sur plusieurs lignes dans le texte aplati :
   recompose le montant complet (ex. « 354 030 680 »), sans jamais inventer de chiffre.
3. Identifie l'ATTRIBUTAIRE PROVISOIRE de chaque lot : souvent une ligne « ✓ Lot N :
   <entreprise> pour un montant minimum TTC de … et un montant maximum TTC de …
   avec un délai d'exécution de … ». Si le lot est déclaré « infructueux », mets
   infructueux=true et laisse l'attributaire vide.

RÈGLES :
- Ne DÉDUIS jamais un montant ; recopie exactement ce qui est écrit (espaces inclus),
  ou null si absent/« -- ».
- Associe chaque soumissionnaire au bon lot (ne mélange pas les lots).
- Réponds UNIQUEMENT par un objet JSON conforme au schéma fourni, sans texte autour.
"""


def build_resultats_prompt(unit: dict) -> str:
    """Construit le message utilisateur : indices déterministes + texte aplati."""
    lots = unit.get("lots") or []
    nb = unit.get("nb_lots_detectes", len(lots))
    indices = [
        f"Émetteur (cadre noir) : {unit.get('emetteur', '?')}",
        f"Macro-section : {unit.get('macro_section', '?')}",
        f"Pages : {unit.get('page_debut')}–{unit.get('page_fin')}",
        f"Bandes « Lot » repérées automatiquement : {nb}",
    ]
    if lots:
        indices.append("Intitulés de lots repérés :")
        indices += [f"  - {l}" for l in lots]
    return (
        "Indices (découpage déterministe, à vérifier) :\n"
        + "\n".join(indices)
        + "\n\nSchéma de sortie attendu (informatif) :\n"
        + json.dumps(RESULTATS_SCHEMA, ensure_ascii=False)
        + "\n\nTexte aplati du tableau à structurer :\n"
        "------------------------------------------------------------\n"
        + (unit.get("flat_text") or "")
        + "\n------------------------------------------------------------\n"
    )


def extract_resultats_unit(unit: dict, max_retries: int = 3,
                           providers: Optional[list] = None) -> Optional[dict]:
    """Envoie une unité-émetteur au LLM pour la structurer en lots +
    soumissionnaires + attributaires. Rend le dict structuré, enrichi de la
    provenance, ou None si tous les providers échouent."""
    prompt = build_resultats_prompt(unit)
    result = call_with_rotation(
        prompt, RESULTATS_SCHEMA, max_retries=max_retries,
        system_prompt=RESULTATS_SYSTEM_PROMPT, strict=False, providers=providers,
    )
    if result is None:
        return None
    for k in ("_numero", "_source_pdf", "_source", "page_debut", "page_fin", "macro_section"):
        if k in unit:
            result.setdefault(k, unit[k])
    # Garde-fou : cohérence entre lots détectés et lots structurés.
    nb_struct = len(result.get("lots") or [])
    result["_nb_lots_detectes"] = unit.get("nb_lots_detectes")
    result["_coherence_lots"] = (
        nb_struct == unit.get("nb_lots_detectes")
        or (unit.get("nb_lots_detectes", 0) <= 1 and nb_struct >= 1)
    )
    return result


if __name__ == "__main__":
    import sys
    from pdf_resultats_extract import segment_resultats
    if len(sys.argv) < 2:
        print("Usage: python resultats_llm.py <pdf>   # affiche le prompt de la 1re unité (sans appel réseau)")
        sys.exit(1)
    units = segment_resultats(sys.argv[1])
    if not units:
        print("Aucune unité RÉSULTATS détectée.")
        sys.exit(0)
    print("=== SYSTEM PROMPT ===")
    print(RESULTATS_SYSTEM_PROMPT)
    print("\n=== USER PROMPT (1re unité) ===")
    print(build_resultats_prompt(units[0])[:2500])
