"""
Layer 4 — Auto-analyse et auto-complétion (révision LLM)

Objectif (demande utilisateur) :
  Après la première extraction (Couche 3), on redonne au LLM le TEXTE INTÉGRAL
  de l'annonce découpée ET les valeurs déjà extraites. Le LLM :
    1. vérifie chaque variable déjà extraite (est-elle pertinente / correcte ?),
    2. corrige celles qui sont fausses,
    3. complète celles qui sont vides mais présentes dans le texte,
    4. ne laisse vide QUE ce qui n'existe réellement pas dans l'annonce.

  Le but : plus aucune valeur vide "par oubli". Une valeur vide devient une
  information en soi ("ce champ n'existe pas dans cette annonce"), tracée
  explicitement (statut "absent") plutôt que subie.

Différence clé avec la Couche 3 :
  - Couche 3 extrait "à partir de zéro".
  - Couche 4 RAISONNE sur une extraction existante (auto-analyse) et la complète.
    Elle est agnostique de ce qui a produit la première passe (regex, LLM, OCR) :
    tant qu'on lui passe un dict de champs + le texte, elle fait son travail.

La sortie ajoute une piste d'audit :
  - `_analyse`          : liste de {champ, statut, justification}
  - `_champs_absents`   : champs jugés réellement absents du texte
  - `_champs_a_verifier`: champs encore vides SANS être marqués "absents"
                          (anomalie -> relecture humaine conseillée)
"""

import json
from typing import Optional

from layer3_extract_llm import load_field_schema, call_with_rotation


# Statuts possibles de l'auto-analyse, par champ.
STATUT_CONFIRME = "confirme"   # la valeur de la 1re passe était juste -> inchangée
STATUT_CORRIGE = "corrige"     # la valeur était fausse/imprécise -> remplacée
STATUT_COMPLETE = "complete"   # la valeur était vide -> trouvée dans le texte
STATUT_ABSENT = "absent"       # l'information n'existe pas dans l'annonce -> null assumé
STATUTS = (STATUT_CONFIRME, STATUT_CORRIGE, STATUT_COMPLETE, STATUT_ABSENT)


REVIEW_SYSTEM_PROMPT = """Tu es un réviseur-vérificateur de données de marchés publics du Burkina Faso.
On te donne le TEXTE INTÉGRAL d'une annonce et un ensemble de valeurs DÉJÀ extraites
par une première passe (qui peut contenir des erreurs, des imprécisions ou des trous).

Ta mission est une AUTO-ANALYSE puis une AUTO-COMPLÉTION de TOUTES les variables, sans exception :
1. Pour CHAQUE champ, confronte la valeur fournie au texte.
   - Si elle est exacte et pertinente -> garde-la (statut "confirme").
   - Si elle est fausse, incomplète ou mal recopiée -> corrige-la d'après le texte (statut "corrige").
   - Si elle est vide MAIS que l'information figure dans le texte -> renseigne-la (statut "complete").
   - Si l'information n'apparaît réellement PAS dans le texte -> laisse null (statut "absent").
2. Tu ne dois JAMAIS inventer. Une valeur ne doit provenir que du texte de l'annonce.
   Une valeur vide est acceptable UNIQUEMENT si le champ est réellement absent ("absent").
3. Recopie les montants, numéros et dates tels qu'écrits (pas de conversion d'unité).

Réponds UNIQUEMENT par un objet JSON de la forme :
{
  "champs": { <tous les champs du schéma avec leur valeur finale ou null> },
  "analyse": [ { "champ": "<nom>", "statut": "confirme|corrige|complete|absent", "justification": "<courte raison citant le texte>" } ]
}
L'objet "champs" doit contenir TOUS les champs demandés, et "analyse" une entrée par champ.
"""


def _is_empty(value) -> bool:
    """Vide = None, chaîne blanche, liste/dict vides. 0 et False ne sont PAS vides."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) == 0
    return False


def build_review_schema(field_defs: dict) -> dict:
    """Schéma de sortie de la révision : `champs` reprend tous les champs métier
    (non contraints en 'required' : la révision doit pouvoir laisser null un champ
    réellement absent), plus un tableau `analyse` traçant la décision par champ."""
    champs_props = {}
    for field_name, spec in field_defs.items():
        prop = {
            "type": spec.get("type", "string"),
            "description": spec.get("description", ""),
        }
        if spec.get("type") == "array":
            prop["items"] = spec.get("items", {"type": "string"})
        champs_props[field_name] = prop

    return {
        "type": "object",
        "properties": {
            "champs": {"type": "object", "properties": champs_props},
            "analyse": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "champ": {"type": "string"},
                        "statut": {"type": "string", "enum": list(STATUTS)},
                        "justification": {"type": "string"},
                    },
                },
            },
        },
        "required": ["champs", "analyse"],
    }


def _format_extracted(extracted: dict, field_defs: dict) -> str:
    """Rend lisible pour le LLM l'état de la première passe, champ par champ,
    en marquant explicitement les trous pour orienter l'auto-complétion."""
    lines = []
    for field_name in field_defs:
        value = extracted.get(field_name)
        if _is_empty(value):
            lines.append(f"- {field_name}: (VIDE — à compléter si présent dans le texte)")
        else:
            lines.append(f"- {field_name}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines)


def build_review_prompt(unit_text: str, extracted: dict, field_defs: dict) -> str:
    schema = build_review_schema(field_defs)
    return (
        "Texte intégral de l'annonce à analyser :\n"
        "------------------------------------------------------------\n"
        f"{unit_text}\n"
        "------------------------------------------------------------\n\n"
        "Valeurs issues de la première extraction (à vérifier, corriger et compléter) :\n"
        f"{_format_extracted(extracted, field_defs)}\n\n"
        "Schéma de sortie attendu (informatif) :\n"
        f"{json.dumps(schema, ensure_ascii=False)}\n"
    )


def merge_review(extracted: dict, review_result: dict, field_defs: dict) -> dict:
    """Fusionne la sortie de révision avec l'extraction d'origine et calcule la
    piste d'audit. On repart de `extracted` pour préserver les métadonnées
    (_page_debut, etc.) et les champs que la révision n'aurait pas renvoyés."""
    champs = review_result.get("champs") or {}
    analyse = review_result.get("analyse") or []

    completed = dict(extracted)
    for field_name in field_defs:
        if field_name in champs:
            completed[field_name] = champs[field_name]

    statut_par_champ = {
        a.get("champ"): a.get("statut")
        for a in analyse
        if isinstance(a, dict) and a.get("champ")
    }

    champs_absents = [
        name for name in field_defs
        if statut_par_champ.get(name) == STATUT_ABSENT
    ]
    # Champs encore vides SANS être déclarés absents : incohérence -> à relire.
    champs_a_verifier = [
        name for name in field_defs
        if _is_empty(completed.get(name)) and name not in champs_absents
    ]

    completed["_analyse"] = analyse
    completed["_champs_absents"] = champs_absents
    completed["_champs_a_verifier"] = champs_a_verifier
    if review_result.get("_provider"):
        completed["_provider_review"] = review_result["_provider"]
    return completed


def review_and_complete(unit_text: str, extracted: dict, section: str,
                        max_retries: int = 3,
                        providers: Optional[list] = None) -> Optional[dict]:
    """Passe d'auto-analyse + auto-complétion.

    Rend un dict enrichi (champs finaux + piste d'audit `_analyse` /
    `_champs_absents` / `_champs_a_verifier`), ou None si tous les providers
    ont échoué (dans ce cas l'appelant conserve l'extraction de la 1re passe).
    """
    field_defs = load_field_schema(section)
    schema = build_review_schema(field_defs)
    prompt = build_review_prompt(unit_text, extracted, field_defs)

    # strict=False : la sortie contient des objets imbriqués libres (montants,
    # contacts...) que le mode "strict" d'OpenRouter rejetterait ; la contrainte
    # réelle est portée par le prompt et revalidée côté Python (merge_review).
    review_result = call_with_rotation(
        prompt, schema, max_retries=max_retries,
        system_prompt=REVIEW_SYSTEM_PROMPT, strict=False, providers=providers,
    )
    if review_result is None:
        return None

    return merge_review(extracted, review_result, field_defs)


if __name__ == "__main__":
    import sys

    # Mode hors-ligne : affiche le prompt EXACT envoyé au LLM pour une annonce,
    # sans aucun appel réseau. Usage : python layer4_review.py <annonce.txt> [SECTION]
    if len(sys.argv) < 2:
        print("Usage: python layer4_review.py <fichier_texte_annonce> [AVIS|RESULTATS]")
        sys.exit(1)

    from pathlib import Path
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    section = sys.argv[2] if len(sys.argv) > 2 else "AVIS"
    field_defs = load_field_schema(section)

    # Démo : première passe supposée vide pour visualiser l'auto-complétion.
    demo_extracted = {name: None for name in field_defs}

    print("=== SYSTEM PROMPT ===")
    print(REVIEW_SYSTEM_PROMPT)
    print("\n=== USER PROMPT ===")
    print(build_review_prompt(text, demo_extracted, field_defs))
