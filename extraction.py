"""
EXTRACTION par DEUX modèles indépendants (A et B).

Les deux modèles reçoivent EXACTEMENT le même texte et le même prompt, et
extraient les champs du schéma (schema_config.yaml). On ne tranche RIEN ici :
A et B partent tels quels au juge, qui compare, arbitre et décide.

Les RÈGLES apprises (regles.py) sont injectées dans le prompt des deux
extracteurs (en amont). Un `hint` ponctuel peut être ajouté pour le réessai
piloté par le juge (3ᵉ issue).

Dégradation gracieuse :
  - 2 providers (ou +) -> A par le 1er, B par le 2ᵉ ;
  - 1 provider          -> A seulement, B = None (le juge passera en mode sévère) ;
  - 0 provider          -> None (l'appelant retombe sur la chaîne déterministe).

Les appels passent par call_with_rotation (rotation de clés / providers). Testé
hors-ligne avec des providers injectés.
"""

import json
import logging
from typing import Optional

from layer3_extract_llm import (
    call_with_rotation, build_providers, load_field_schema, build_json_schema,
)
import regles

log = logging.getLogger("scrapperbf.extraction")


EXTRACT_SYSTEM = """Tu extrais les champs d'un avis de marché public du Burkina Faso (DGCMEF).
Distingue clairement les valeurs de l'ÉMETTEUR (autorité contractante / émettrice) et
celles de l'ANNONCE (objet, type, référence, financement, montant, dates, délais, lots).
Règles : n'invente RIEN (null si absent) ; recopie montants, numéros et dates tels
qu'écrits ; classe le montant prévisionnel en HTVA / TTC quand les deux existent.
Réponds UNIQUEMENT par un objet JSON conforme au schéma."""


def _build_system(regles_applicables: list, hint: str = "") -> str:
    """Prompt système commun aux deux extracteurs : socle + règles apprises
    applicables à ce texte + hint ponctuel (réessai). Les règles sont matchées
    une seule fois par l'appelant et passées ici."""
    bloc_regles = regles.format_for_prompt(regles_applicables)
    parts = [EXTRACT_SYSTEM]
    if bloc_regles:
        parts.append(bloc_regles)
    if hint:
        parts.append("CONSIGNE CIBLÉE POUR CE TEXTE :\n" + hint)
    return "\n\n".join(parts)


def _extract_one(text: str, schema: dict, provider: dict, system: str,
                 max_retries: int) -> Optional[dict]:
    prompt = (f"Schéma attendu (informatif) :\n{json.dumps(schema, ensure_ascii=False)}\n\n"
              f"Texte de l'annonce :\n{text}")
    return call_with_rotation(prompt, schema, max_retries=max_retries,
                              system_prompt=system, strict=False, providers=[provider])


def extract_fields(text: str, section: str = "AVIS", providers: Optional[list] = None,
                   hint: str = "", max_retries: int = 2) -> Optional[dict]:
    """Lance les deux extractions A et B sur le même texte/prompt.

    Retourne {"a", "b", "providers", "section", "regles_appliquees", "degrade"} ou
    None si aucun provider configuré. `b` est None en mode 1 provider."""
    provs = providers if providers is not None else build_providers()
    if not provs:
        return None

    field_defs = load_field_schema(section)
    schema = build_json_schema(field_defs)
    appliquees = regles.match_rules(text, section)   # matché une seule fois
    system = _build_system(appliquees, hint)

    a = _extract_one(text, schema, provs[0], system, max_retries) or {}
    if len(provs) > 1:
        b = _extract_one(text, schema, provs[1], system, max_retries) or {}
    else:
        b = None  # un seul modèle disponible : pas de second avis

    if not a and not b:
        log.warning("extraction : A et B ont échoué")
        return None

    degrade = b is None or not b
    if degrade:
        log.info("extraction : mode dégradé (un seul extracteur) -> juge sévère")

    return {
        "a": a,
        "b": b,
        "section": section,
        "providers": [p.get("name") for p in provs[:2] if isinstance(p, dict)],
        "regles_appliquees": [r.get("id") for r in appliquees],
        "degrade": degrade,
    }
