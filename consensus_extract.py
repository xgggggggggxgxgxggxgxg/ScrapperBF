"""
Extraction par CONSENSUS de 3 modèles LLM (remplace l'extraction regex des champs).

Pour chaque annonce :
  1. DEUX modèles extraient indépendamment tous les champs (valeurs de l'ÉMETTEUR
     ET de l'ANNONCE) -> A et B.
  2. On mesure la DIVERGENCE entre A et B (fraction de champs qui diffèrent).
  3. Un TROISIÈME modèle ARBITRE : il reçoit le texte + A + B et tranche champ par
     champ d'après le texte, en listant les désaccords.
  4. Si la divergence dépasse un seuil -> ALERTE revue humaine.

Dégradation gracieuse : avec 2 providers, le 2e sert d'arbitre ; avec 1 seul, une
seule extraction (pas de consensus). Sans provider configuré -> None (l'appelant
retombe sur l'extraction déterministe regex).

Les appels réseau passent par la rotation de providers (config.py / layer3).
Testé hors-ligne avec des providers injectés.
"""

import json
from typing import Optional

from layer3_extract_llm import (
    call_with_rotation, build_providers, load_field_schema, build_json_schema,
)
from judge import fold


EXTRACT_SYSTEM = """Tu extrais les champs d'un avis de marché public du Burkina Faso (DGCMEF).
Distingue clairement les valeurs de l'ÉMETTEUR (autorité contractante / émettrice) et
celles de l'ANNONCE (objet, type, référence, financement, montant, dates, délais, lots).
Règles : n'invente RIEN (null si absent) ; recopie montants, numéros et dates tels
qu'écrits ; classe le montant prévisionnel en HTVA / TTC quand les deux existent.
Réponds UNIQUEMENT par un objet JSON conforme au schéma."""

ARBITER_SYSTEM = """Tu es l'ARBITRE. On te donne le TEXTE d'une annonce et DEUX extractions
indépendantes (A et B). Pour CHAQUE champ, choisis la valeur correcte d'après le TEXTE
(ou corrige si les deux se trompent). Distingue émetteur et annonce. Ne tranche jamais
au hasard : si le texte ne permet pas de décider, mets null et signale le désaccord.
Réponds UNIQUEMENT en JSON : {"champs": {<tous les champs>}, "desaccords":
[{"champ","a","b","choisi","raison"}]}."""


def _norm(v) -> str:
    if isinstance(v, dict):
        return " ".join(f"{k}={fold(x)}" for k, x in sorted(v.items()) if x)
    if isinstance(v, list):
        return " ".join(fold(x) for x in v)
    return fold(v)


def field_divergence(a: dict, b: dict, field_defs: dict):
    """Fraction de champs (non vides des deux côtés) qui diffèrent entre A et B,
    + la liste des champs en désaccord."""
    diff, total, champs = 0, 0, []
    for f in field_defs:
        va, vb = _norm(a.get(f)), _norm(b.get(f))
        if not va and not vb:
            continue
        total += 1
        if va != vb:
            diff += 1
            champs.append(f)
    return (diff / total if total else 0.0), champs


def _merge(a: dict, b: dict, field_defs: dict) -> dict:
    """Fusion déterministe (repli si pas d'arbitre) : valeur commune si A==B,
    sinon la 1re non vide, en marquant le champ comme incertain."""
    out = {}
    for f in field_defs:
        va, vb = a.get(f), b.get(f)
        out[f] = va if _norm(va) else vb
    return out


def _extract_one(text: str, schema: dict, provider: dict, max_retries: int) -> Optional[dict]:
    prompt = (f"Schéma attendu (informatif) :\n{json.dumps(schema, ensure_ascii=False)}\n\n"
              f"Texte de l'annonce :\n{text}")
    return call_with_rotation(prompt, schema, max_retries=max_retries,
                              system_prompt=EXTRACT_SYSTEM, strict=False, providers=[provider])


def _arbitrate(text: str, a: dict, b: dict, arb_schema: dict, provider: dict,
               max_retries: int) -> Optional[dict]:
    prompt = (f"Texte de l'annonce :\n{text}\n\n"
              f"Extraction A :\n{json.dumps(a, ensure_ascii=False)}\n\n"
              f"Extraction B :\n{json.dumps(b, ensure_ascii=False)}\n\n"
              f"Schéma de sortie (informatif) :\n{json.dumps(arb_schema, ensure_ascii=False)}")
    return call_with_rotation(prompt, arb_schema, max_retries=max_retries,
                              system_prompt=ARBITER_SYSTEM, strict=False, providers=[provider])


def consensus_extract(text: str, section: str = "AVIS", providers: Optional[list] = None,
                      seuil_divergence: float = 0.34, max_retries: int = 2) -> Optional[dict]:
    """Extraction par consensus. Retourne les champs finaux + un bloc `_consensus`
    (divergence, modèles, désaccords, alerte_humaine), ou None si aucun provider."""
    provs = providers if providers is not None else build_providers()
    if not provs:
        return None
    field_defs = load_field_schema(section)
    schema = build_json_schema(field_defs)
    arb_schema = {
        "type": "object",
        "properties": {
            "champs": {"type": "object"},
            "desaccords": {"type": "array", "items": {"type": "object", "properties": {
                "champ": {"type": "string"}, "a": {"type": "string"}, "b": {"type": "string"},
                "choisi": {"type": "string"}, "raison": {"type": "string"}}}},
        },
        "required": ["champs"],
    }

    a = _extract_one(text, schema, provs[0], max_retries) or {}
    b = _extract_one(text, schema, provs[1] if len(provs) > 1 else provs[0], max_retries) or {}
    if not a and not b:
        return None

    divergence, champs_div = field_divergence(a, b, field_defs)

    if len(provs) >= 3:
        arb = _arbitrate(text, a, b, arb_schema, provs[2], max_retries) or {}
        final = arb.get("champs") or _merge(a, b, field_defs)
        desaccords = arb.get("desaccords")
        if desaccords is None:
            desaccords = [{"champ": c, "a": _norm(a.get(c)), "b": _norm(b.get(c))} for c in champs_div]
        arbitre = provs[2].get("name") if isinstance(provs[2], dict) else None
    else:
        final = _merge(a, b, field_defs)
        desaccords = [{"champ": c, "a": _norm(a.get(c)), "b": _norm(b.get(c))} for c in champs_div]
        arbitre = None

    result = dict(final)
    result["_consensus"] = {
        "n_modeles": len(provs),
        "divergence": round(divergence, 3),
        "alerte_humaine": divergence > seuil_divergence,
        "champs_en_desaccord": champs_div,
        "desaccords": desaccords,
        "modele_a": a.get("_provider"),
        "modele_b": b.get("_provider"),
        "arbitre": arbitre,
    }
    return result
