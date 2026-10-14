"""
Layer 3 — Extraction sémantique modulable (LLM + schéma configurable)

Principe : le "quoi extraire" vit dans schema_config.yaml, pas dans le code.
Ajouter un champ = éditer le YAML. Le code ne change jamais pour un nouveau champ.

Rotation multi-provider (Gemini / OpenRouter / Mistral) :
- tous exposent une API compatible "chat completions" avec sortie JSON structurée
- on essaie le provider primaire, on bascule sur le suivant en cas d'erreur/quota
- chaque provider a son propre rate limit -> la rotation sert aussi de load balancing
"""

import json
import os
import time
import yaml
import requests
from pathlib import Path
from typing import Optional


CONFIG_PATH = Path(__file__).parent / "schema_config.yaml"


def load_field_schema(section: str) -> dict:
    """Charge la définition des champs à extraire pour une section donnée
    (RESULTATS ou AVIS) depuis le YAML. Le JSON Schema envoyé au LLM est
    généré dynamiquement à partir de cette config."""
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return cfg[section]


def build_json_schema(field_defs: dict) -> dict:
    properties = {}
    required = []
    for field_name, spec in field_defs.items():
        properties[field_name] = {
            "type": spec.get("type", "string"),
            "description": spec.get("description", ""),
        }
        if spec.get("type") == "array":
            properties[field_name]["items"] = spec.get("items", {"type": "string"})
        if spec.get("required"):
            required.append(field_name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


SYSTEM_PROMPT = """Tu es un extracteur de données structurées pour des avis de marchés publics
du Burkina Faso. Tu reçois le texte brut d'UNE unité (un marché ou un avis).
Extrait UNIQUEMENT les champs demandés dans le schéma JSON fourni.
Règles strictes :
- Si une information est absente du texte, mets null. N'invente JAMAIS de valeur.
- Les montants doivent être recopiés tels qu'écrits (ne convertis pas les unités).
- Réponds UNIQUEMENT avec un objet JSON valide conforme au schéma, sans texte autour.
"""


# --- Providers -------------------------------------------------------------

class ProviderError(Exception):
    pass


def call_gemini(prompt: str, json_schema: dict, api_key: str) -> dict:
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
    resp = requests.post(
        f"{url}?key={api_key}",
        json={
            "contents": [{"parts": [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": json_schema,
            },
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise ProviderError(f"Gemini {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def call_openrouter(prompt: str, json_schema: dict, api_key: str, model: str) -> dict:
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,  # ex: "mistralai/mistral-large" ou tout modèle dispo sur OpenRouter
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": json_schema, "strict": True},
            },
        },
        timeout=60,
    )
    if resp.status_code != 200:
        raise ProviderError(f"OpenRouter {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    return json.loads(text)


PROVIDERS = [
    # Ordre = ordre d'essai. On bascule au suivant si le précédent échoue.
    # Clés API lues depuis les variables d'environnement (voir .env.example).
    # 1. Le modèle personnalisé OpenRouter
    {"name": "openrouter-xiaomi", "fn": lambda prompt, schema: call_openrouter(
        prompt, schema, os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("OPENROUTER_XIAOMI_MODEL", "xiaomi/mimo-v2.5"))},

    # 2. Gemini en solution de repli
    {"name": "gemini", "fn": lambda prompt, schema: call_gemini(
        prompt, schema, os.environ.get("GEMINI_API_KEY", ""))},

    # 3. Mistral fallback
    {"name": "openrouter-mistral", "fn": lambda prompt, schema: call_openrouter(
        prompt, schema, os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("OPENROUTER_MISTRAL_MODEL", "mistralai/mistral-large"))},
]


def extract_unit(
    unit_text: str,
    section: str,
    max_retries: int = 3,
    extra_instructions: str = "",
) -> Optional[dict]:
    """Extrait une unité via le premier provider disponible.

    ``extra_instructions`` reçoit les consignes dérivées des corrections
    humaines (``FeedbackStore.prompt_supplement``) : les champs que des
    opérateurs ont déjà dû corriger sur ce type d'annonce sont signalés au
    modèle, ce qui ferme la boucle entre le dashboard et l'extraction.
    """
    field_defs = load_field_schema(section)
    json_schema = build_json_schema(field_defs)
    parts = [
        "Schéma attendu (informatif, la contrainte réelle est appliquée par l'API):",
        json.dumps(json_schema, ensure_ascii=False),
    ]
    if extra_instructions:
        parts += ["", "Points de vigilance issus de relectures humaines:", extra_instructions]
    parts += ["", "Texte à analyser:", unit_text]
    prompt = "\n".join(parts)

    last_error = None
    for provider in PROVIDERS:
        for attempt in range(max_retries):
            try:
                result = provider["fn"](prompt, json_schema)
                if not isinstance(result, dict):
                    # Un modèle peut rendre un JSON valide mais pas un objet
                    # (liste, chaîne) : on le traite comme un échec du provider
                    # plutôt que de laisser planter l'appelant plus loin.
                    raise ProviderError(f"réponse non conforme: {type(result).__name__}")
                result["_provider"] = provider["name"]
                return result
            except ProviderError as e:
                last_error = e
                time.sleep(2 ** attempt)  # backoff exponentiel
            except Exception as e:  # rate limit, timeout réseau, etc.
                last_error = e
                time.sleep(2 ** attempt)
        # provider épuisé -> on passe au suivant

    print(f"[ECHEC] toutes les tentatives ont échoué pour cette unité: {last_error}")
    return None


if __name__ == "__main__":
    import sys
    # Chercher layer2_output.json d'abord dans new_logic, sinon dans le dossier courant
    base_dir = Path(__file__).parent
    json_path = base_dir / "new_logic" / "layer2_output.json"
    if not json_path.exists():
        json_path = base_dir / "layer2_output.json"
        
    data = json.load(open(json_path, encoding="utf-8"))
    
    # Chercher la première unité disponible (dans RESULTATS ou AVIS)
    if data.get("RESULTATS") and len(data["RESULTATS"]) > 0:
        unit = data["RESULTATS"][0]
        section = "RESULTATS"
    elif data.get("AVIS") and len(data["AVIS"]) > 0:
        unit = data["AVIS"][0]
        section = "AVIS"
    else:
        print("Erreur : Aucune unité trouvée dans le fichier JSON (ni dans RESULTATS, ni dans AVIS).")
        sys.exit(1)
        
    print(f"Extraction de la première unité ({section})...")
    result = extract_unit(unit["flat_text"], section)
    print(json.dumps(result, ensure_ascii=False, indent=2))
