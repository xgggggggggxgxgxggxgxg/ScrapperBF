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
import re
import time
import logging
import yaml
import requests
from pathlib import Path
from typing import Optional

import config  # charge .env et décrit les providers configurés (clés présentes)
import token_tracker  # comptage des tokens par provider/modèle/clé

log = logging.getLogger("scrapperbf.llm")


# Timeout des appels HTTP : (connexion, lecture). Réglable via LLM_TIMEOUT (s) ;
# coupe un modèle qui « réfléchit » sans fin au lieu d'attendre 10 minutes.
_T = float(os.environ.get("LLM_TIMEOUT", "60"))
LLM_TIMEOUT = (10, _T)


def _attach_usage(result, kind: str, raw: dict):
    """Attache l'usage (tokens) normalisé au résultat, pour le comptage."""
    if isinstance(result, dict):
        result["_usage"] = token_tracker.normalize_usage(kind, raw or {})
    return result


def _loads_lenient(text: str) -> dict:
    """Parse du JSON même quand le modèle l'enrobe (``` ```json fences, préambule).
    Beaucoup de modèles gratuits ne respectent pas le JSON strict."""
    if text is None:
        raise ProviderError("réponse vide")
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)  # bloc markdown
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    m = re.search(r"\{.*\}", text, re.S)                   # 1er objet {...}
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    raise ProviderError("réponse non-JSON : " + str(text)[:160])


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


def call_gemini(prompt: str, json_schema: dict, api_key: str,
                model: str = "gemini-2.0-flash",
                system_prompt: str = SYSTEM_PROMPT, strict: bool = True) -> dict:
    # Gemini n'a pas de mode "strict" distinct : `strict` est accepté pour
    # homogénéiser la signature des providers, mais ignoré ici.
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    resp = requests.post(
        f"{url}?key={api_key}",
        json={
            "contents": [{"parts": [{"text": system_prompt + "\n\n" + prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": json_schema,
            },
        },
        timeout=LLM_TIMEOUT,
    )
    if resp.status_code != 200:
        raise ProviderError(f"Gemini {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return _attach_usage(_loads_lenient(text), "gemini", data.get("usageMetadata"))


_FORMAT_REJECT_RE = re.compile(
    r"response_format|json_schema|structured|schema|not\s*support|unsupported|invalid|400|422", re.I)


def call_openrouter(prompt: str, json_schema: dict, api_key: str, model: str,
                    system_prompt: str = SYSTEM_PROMPT, strict: bool = True) -> dict:
    """Appel OpenRouter TOLÉRANT : beaucoup de modèles gratuits refusent le
    `response_format: json_schema`. On tente, dans l'ordre : json_schema →
    json_object → sans format (JSON porté par le prompt). Le JSON est parsé
    même enrobé de ``` fences."""
    base = {"model": model,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": prompt}]}
    payloads = [
        {**base, "response_format": {"type": "json_schema",
                                     "json_schema": {"name": "extraction", "schema": json_schema, "strict": strict}}},
        {**base, "response_format": {"type": "json_object"}},
        base,
    ]
    last = ""
    for pl in payloads:
        resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                             headers={"Authorization": f"Bearer {api_key}",
                                      "Content-Type": "application/json"},
                             json=pl, timeout=LLM_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
            return _attach_usage(_loads_lenient(text), "openrouter", data.get("usage"))
        last = f"OpenRouter {resp.status_code}: {resp.text[:300]}"
        if _is_quota(last):          # quota/limite -> laisse la rotation basculer de clé
            raise ProviderError(last)
        if not _FORMAT_REJECT_RE.search(last):   # erreur non liée au format (auth, slug) -> stop
            break
    raise ProviderError(last)


def call_openai_compatible(prompt: str, json_schema: dict, api_key: str, base_url: str,
                           model: str, system_prompt: str = SYSTEM_PROMPT,
                           strict: bool = True) -> dict:
    """Appelle N'IMPORTE QUEL endpoint compatible OpenAI (/chat/completions) :
    serveur local (Ollama, LM Studio, vLLM), Groq, Together, etc. On demande une
    sortie JSON (`json_object`, largement supporté) ; le schéma est porté par le
    prompt. `base_url` ex. http://localhost:11434/v1 ou https://api.groq.com/openai/v1."""
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        },
        timeout=LLM_TIMEOUT,
    )
    if resp.status_code != 200:
        raise ProviderError(f"OpenAI-compat {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    return _attach_usage(_loads_lenient(text), "openai_compat", data.get("usage"))


# Signature unifiée des appels : fn(prompt, schema, system_prompt, strict) -> dict
def _build_call(spec: dict, key: str):
    """Construit l'appelable d'un provider pour UNE clé donnée."""
    kind, model = spec["kind"], spec["model"]
    if kind == "openrouter":
        return lambda p, sc, sy, st: call_openrouter(p, sc, key, model, system_prompt=sy, strict=st)
    if kind == "gemini":
        return lambda p, sc, sy, st: call_gemini(p, sc, key, model=model, system_prompt=sy, strict=st)
    if kind == "openai_compat":
        base = spec["base_url"]
        return lambda p, sc, sy, st: call_openai_compatible(p, sc, key, base, model, system_prompt=sy, strict=st)
    raise ValueError(f"type de provider inconnu: {kind}")


def _make_provider(spec: dict) -> dict:
    """Provider à partir d'une spec de config.py. Porte TOUTES ses clés (rotation
    en cas de quota/erreur) ; `fn` (1re clé) est conservé pour compatibilité."""
    keys = spec.get("keys") or [spec["key"]]
    return {"name": spec["name"], "kind": spec["kind"], "model": spec["model"],
            "keys": keys, "_spec": spec, "fn": _build_call(spec, keys[0])}


# Erreurs de quota / limite de débit -> on bascule IMMÉDIATEMENT de clé (sans
# épuiser les retries sur une clé déjà à sec).
_QUOTA_RE = re.compile(r"\b429\b|quota|rate.?limit|too many requests|insufficient|exhausted|resource.?exhausted", re.I)


def _is_quota(err) -> bool:
    return bool(_QUOTA_RE.search(str(err)))


def build_providers(specs: Optional[list] = None) -> list:
    """Liste des providers ACTIFS (dans l'ordre d'essai), déduite des clés
    présentes dans l'environnement / .env (voir config.py)."""
    specs = specs if specs is not None else config.provider_specs()
    return [_make_provider(s) for s in specs]


# Construit au chargement à partir de .env. Reconstruire avec build_providers()
# si l'environnement change en cours d'exécution.
PROVIDERS = build_providers()


def call_with_rotation(prompt: str, json_schema: dict, max_retries: int = 3,
                       system_prompt: str = SYSTEM_PROMPT, strict: bool = True,
                       providers: Optional[list] = None) -> Optional[dict]:
    """Cœur réutilisable : essaie chaque provider dans l'ordre, avec backoff
    exponentiel par provider, puis bascule au suivant. Utilisé à la fois par
    l'extraction (Couche 3) et par la révision/auto-complétion (Couche 4).

    `providers` permet d'injecter une liste alternative (tests hors-ligne)."""
    providers = providers if providers is not None else PROVIDERS
    last_error = None
    for provider in providers:
        spec = provider.get("_spec")
        keys = provider.get("keys") or [None]
        for key in keys:
            # fn lié à CETTE clé (sinon fn legacy du provider, pour les tests).
            fn = _build_call(spec, key) if spec else provider["fn"]
            quota_hit = False
            for attempt in range(max_retries):
                try:
                    result = fn(prompt, json_schema, system_prompt, strict)
                    if not isinstance(result, dict):
                        raise ProviderError(f"réponse non-objet: {type(result).__name__}")
                    usage = result.pop("_usage", None)
                    if spec and usage:
                        token_tracker.record(spec["kind"], spec["model"], key, usage)
                    result["_provider"] = provider["name"]
                    if attempt or key is not keys[0] or provider is not providers[0]:
                        log.info("provider %s (clé %s) : succès (tentative %d)",
                                 provider["name"], token_tracker.mask(key), attempt + 1)
                    return result
                except Exception as e:  # ProviderError, quota, réseau, JSON invalide…
                    last_error = e
                    if spec:
                        token_tracker.record_error(spec["kind"], spec["model"], key, str(e)[:80])
                    if _is_quota(e):
                        log.warning("provider %s (clé %s) : quota/limite -> bascule clé/provider",
                                    provider["name"], token_tracker.mask(key))
                        quota_hit = True
                        break  # inutile d'insister sur une clé à sec
                    log.warning("provider %s (clé %s) : erreur (tentative %d) : %s",
                                provider["name"], token_tracker.mask(key), attempt + 1, str(e)[:120])
                    time.sleep(2 ** attempt)  # backoff exponentiel
            # clé suivante (quota ou retries épuisés)
        # provider suivant

    log.error("toutes les tentatives ont échoué sur %d provider(s) : %s",
              len(providers), last_error)
    return None


def extract_unit(unit_text: str, section: str, max_retries: int = 3,
                 providers: Optional[list] = None) -> Optional[dict]:
    field_defs = load_field_schema(section)
    json_schema = build_json_schema(field_defs)
    prompt = f"Schéma attendu (informatif, la contrainte réelle est appliquée par l'API):\n{json.dumps(json_schema, ensure_ascii=False)}\n\nTexte à analyser:\n{unit_text}"
    return call_with_rotation(
        prompt, json_schema, max_retries=max_retries,
        system_prompt=SYSTEM_PROMPT, strict=True, providers=providers,
    )


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
