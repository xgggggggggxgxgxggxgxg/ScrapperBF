"""
JUGE-LLM — la moitié SÉMANTIQUE du juge (3ᵉ modèle).

On lui donne le TEXTE de l'annonce et les DEUX extractions (A et B). Il doit :
  1. pour chaque champ, choisir la valeur correcte d'après le TEXTE (ou corriger
     si les deux se trompent) ;
  2. repérer et EXPLIQUER les divergences entre A et B (« comprendre ce qui s'est
     passé ») ;
  3. rendre un verdict global : "bon" | "doute" | "mauvais".

Il tourne EN PARALLÈLE du scoreur (code pur). L'orchestrateur (juge.py) compare
ensuite les deux verdicts : s'ils s'opposent -> rouge.

Sortie JSON :
  {
    "champs": { ...valeurs finales... },
    "divergences": [{"champ","a","b","choisi","raison"}],
    "corrections": [{"champ","ancienne","nouvelle","raison"}],
    "verdict": "bon|doute|mauvais",
    "analyse": "<synthèse courte de ce qui s'est passé>"
  }

Renvoie None si aucun provider (l'orchestrateur passe alors en scoreur seul,
mode sévère). Testable hors-ligne (provider injecté).
"""

import json
import logging
from typing import Optional

from layer3_extract_llm import (
    call_with_rotation, build_providers, load_field_schema, build_json_schema,
)

log = logging.getLogger("scrapperbf.juge_llm")


JUGE_SYSTEM = """Tu es le JUGE. On te donne le TEXTE d'une annonce de marché public (Burkina
Faso / DGCMEF) et DEUX extractions indépendantes, A et B, des mêmes champs.

Ta mission :
1. Pour CHAQUE champ, choisis la valeur CORRECTE strictement d'après le TEXTE.
   Si A et B se trompent tous les deux, corrige d'après le texte. Si le texte ne
   permet pas de trancher, mets null et signale-le.
2. EXPLIQUE les divergences entre A et B : qu'est-ce qui a causé l'écart
   (faute de lecture, montant HT vs TTC, champs intervertis, valeur inventée…).
3. Donne un VERDICT global sur la fiabilité de l'extraction finale :
   - "bon"     : tout concorde avec le texte, rien d'inquiétant ;
   - "doute"   : un ou deux champs incertains, mais rien de grave ;
   - "mauvais" : contradictions sérieuses, texte peu exploitable, champs clés faux.
N'invente jamais : une valeur absente du texte reste null.
Réponds UNIQUEMENT en JSON conforme au schéma fourni."""

# Schéma de sortie du juge (informatif pour le modèle ; la vraie contrainte est
# portée par le prompt — beaucoup de modèles gratuits ignorent json_schema).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "champs": {"type": "object"},
        "divergences": {"type": "array", "items": {"type": "object", "properties": {
            "champ": {"type": "string"}, "a": {"type": "string"}, "b": {"type": "string"},
            "choisi": {"type": "string"}, "raison": {"type": "string"}}}},
        "corrections": {"type": "array", "items": {"type": "object", "properties": {
            "champ": {"type": "string"}, "ancienne": {"type": "string"},
            "nouvelle": {"type": "string"}, "raison": {"type": "string"}}}},
        "verdict": {"type": "string", "enum": ["bon", "doute", "mauvais"]},
        "analyse": {"type": "string"},
    },
    "required": ["champs", "verdict"],
}

_VERDICTS = {"bon", "doute", "mauvais"}


def review(text: str, a: dict, b: Optional[dict], section: str = "AVIS",
           providers: Optional[list] = None, max_retries: int = 2) -> Optional[dict]:
    """Fait trancher et expliquer le juge-LLM. `b` peut être None (un seul
    extracteur) : le juge valide alors A contre le texte."""
    provs = providers if providers is not None else build_providers()
    if not provs:
        return None

    field_defs = load_field_schema(section)
    champs_schema = build_json_schema(field_defs)

    prompt = (
        f"Texte de l'annonce :\n{text}\n\n"
        f"Extraction A :\n{json.dumps(a or {}, ensure_ascii=False)}\n\n"
        f"Extraction B :\n{json.dumps(b if b else {}, ensure_ascii=False)}\n\n"
        f"Champs attendus (schéma informatif) :\n{json.dumps(champs_schema, ensure_ascii=False)}\n\n"
        f"Schéma de ta réponse (informatif) :\n{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False)}"
    )
    out = call_with_rotation(prompt, OUTPUT_SCHEMA, max_retries=max_retries,
                             system_prompt=JUGE_SYSTEM, strict=False, providers=provs)
    if out is None:
        return None

    # Normalisation défensive (modèles imparfaits).
    champs = out.get("champs")
    if not isinstance(champs, dict) or not champs:
        # Repli : si le juge n'a pas renvoyé de champs, on garde A (ou B).
        champs = dict(a or b or {})
    verdict = out.get("verdict")
    if verdict not in _VERDICTS:
        verdict = "doute"
    return {
        "champs": {k: v for k, v in champs.items() if not str(k).startswith("_")},
        "divergences": out.get("divergences") or [],
        "corrections": out.get("corrections") or [],
        "verdict": verdict,
        "analyse": out.get("analyse") or "",
        "_provider": out.get("_provider"),
    }
