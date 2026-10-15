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
4. Pour CHAQUE champ, donne une "confiance" entre 0 et 1. Si tu n'es pas certain à 100 %,
   tu DOIS expliquer précisément POURQUOI dans "justification" (ambiguïté, texte illisible,
   plusieurs valeurs candidates, formulation inhabituelle...). Ces raisons servent à
   améliorer les extractions futures : sois concret et actionnable.

Réponds UNIQUEMENT par un objet JSON de la forme :
{
  "champs": { <tous les champs du schéma avec leur valeur finale ou null> },
  "analyse": [ { "champ": "<nom>", "statut": "confirme|corrige|complete|absent", "confiance": <0..1>, "justification": "<raison citant le texte ; obligatoire si confiance < 1>" } ]
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
                        "confiance": {"type": "number"},
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


def build_review_prompt(unit_text: str, extracted: dict, field_defs: dict,
                        feedback_hint: str = "") -> str:
    schema = build_review_schema(field_defs)
    hint_block = ""
    if feedback_hint:
        # Rétroaction : les écarts des traitements précédents sont réinjectés ici
        # pour que le modèle évite de reproduire les mêmes erreurs.
        hint_block = (
            "Retour d'expérience des extractions précédentes "
            "(erreurs récurrentes à éviter cette fois) :\n"
            f"{feedback_hint}\n\n"
        )
    return (
        f"{hint_block}"
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


def _confiance_of(entry: dict) -> Optional[float]:
    c = entry.get("confiance")
    if isinstance(c, bool):  # bool est un int en Python -> on l'exclut
        return None
    if isinstance(c, (int, float)):
        return max(0.0, min(1.0, float(c)))
    return None


def compute_exactitude(completed: dict, field_defs: dict) -> dict:
    """Calcule un score d'exactitude déterministe [0..100] et, si < 100,
    la LISTE DES RAISONS de l'écart (champ par champ), exploitable comme
    signal d'amélioration pour les traitements suivants.

    Philosophie : la confiance auto-déclarée ne fait que moduler un champ
    déjà présent ; les vrais zéros viennent de faits vérifiables (champ requis
    absent, champ vide non résolu). Un champ corrigé reste fiable MAIS génère
    une raison (le modèle s'est trompé en 1re passe -> à apprendre)."""
    analyse = completed.get("_analyse") or []
    info = {a.get("champ"): a for a in analyse if isinstance(a, dict) and a.get("champ")}
    absents = set(completed.get("_champs_absents") or [])
    a_verifier = set(completed.get("_champs_a_verifier") or [])

    fiabilites = []
    raisons = []
    for name, spec in field_defs.items():
        entry = info.get(name, {})
        statut = entry.get("statut")
        conf = _confiance_of(entry)
        justif = (entry.get("justification") or "").strip()
        required = bool(spec.get("required"))

        fiab = 1.0
        raison = None

        if name in a_verifier:
            fiab = 0.0
            raison = "champ vide non résolu (ni trouvé dans le texte, ni déclaré absent)"
        elif name in absents:
            if required:
                fiab = 0.0
                raison = "champ REQUIS absent du texte de l'annonce"
            else:
                fiab = 1.0  # absence légitime d'un champ optionnel : pas un défaut
        elif statut == STATUT_CORRIGE:
            fiab = conf if conf is not None else 0.9
            raison = f"valeur de 1re passe erronée puis corrigée : {justif}".strip()
        elif statut in (STATUT_CONFIRME, STATUT_COMPLETE):
            fiab = conf if conf is not None else 1.0
            if conf is not None and conf < 1.0:
                raison = f"confiance déclarée {conf:.2f} : {justif}".strip()
        else:
            # Aucune analyse explicite pour ce champ.
            if _is_empty(completed.get(name)):
                fiab = 0.0 if required else 0.7
                raison = "champ non analysé et laissé vide"
            else:
                fiab = 0.9
                raison = "champ renseigné mais non analysé explicitement"

        fiabilites.append(fiab)
        if fiab < 1.0 and raison:
            raisons.append({
                "champ": name,
                "statut": statut,
                "confiance": conf,
                "fiabilite": round(fiab, 2),
                "raison": raison,
            })

    score = round(100 * sum(fiabilites) / len(fiabilites)) if fiabilites else 0
    return {"score": score, "raisons": raisons}


def review_and_complete(unit_text: str, extracted: dict, section: str,
                        max_retries: int = 3,
                        providers: Optional[list] = None,
                        feedback_hint: str = "") -> Optional[dict]:
    """Passe d'auto-analyse + auto-complétion.

    Rend un dict enrichi (champs finaux + piste d'audit `_analyse` /
    `_champs_absents` / `_champs_a_verifier`), ou None si tous les providers
    ont échoué (dans ce cas l'appelant conserve l'extraction de la 1re passe).
    """
    field_defs = load_field_schema(section)
    schema = build_review_schema(field_defs)
    prompt = build_review_prompt(unit_text, extracted, field_defs, feedback_hint=feedback_hint)

    # strict=False : la sortie contient des objets imbriqués libres (montants,
    # contacts...) que le mode "strict" d'OpenRouter rejetterait ; la contrainte
    # réelle est portée par le prompt et revalidée côté Python (merge_review).
    review_result = call_with_rotation(
        prompt, schema, max_retries=max_retries,
        system_prompt=REVIEW_SYSTEM_PROMPT, strict=False, providers=providers,
    )
    if review_result is None:
        return None

    completed = merge_review(extracted, review_result, field_defs)

    # Score d'exactitude + raisons de l'écart (si < 100 %) -> signal d'amélioration.
    exactitude = compute_exactitude(completed, field_defs)
    completed["_score_exactitude"] = exactitude["score"]
    if exactitude["score"] < 100:
        completed["_raisons_ecart"] = exactitude["raisons"]
    return completed


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
