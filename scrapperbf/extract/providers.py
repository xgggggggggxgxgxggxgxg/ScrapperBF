"""Fournisseurs LLM, en privilégiant les offres gratuites.

Le projet traite des dizaines d'annonces par bulletin et plusieurs années
d'archives : le coût par appel décide de la faisabilité. On s'appuie donc sur
les paliers gratuits, avec deux conséquences assumées dans la conception :

* **ils sont limités en débit** — d'où la rotation : chaque fournisseur a son
  propre quota, et basculer sur le suivant sert autant de répartition de charge
  que de secours ;
* **ils sont instables** — un modèle gratuit disparaît ou change de nom sans
  préavis. Les identifiants de modèles sont donc dans l'environnement, pas dans
  le code, et un fournisseur sans clé est simplement ignoré au lieu de faire
  échouer l'appel.

Aucune clé n'est écrite ici. Un fournisseur dont la variable d'environnement
est absente n'est pas tenté.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import requests

TIMEOUT = 90
MAX_RETRIES = 2
BACKOFF_BASE = 2.0
# Codes qui valent la peine d'attendre : quota momentané ou panne passagère.
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class ProviderError(RuntimeError):
    """Échec imputable au fournisseur, pas à la requête."""


@dataclass
class Provider:
    """Un fournisseur interrogeable."""

    name: str
    env_key: str
    call: Callable[[str, str, dict, str], dict]
    model_env: str = ""
    default_model: str = ""
    free: bool = True

    @property
    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    @property
    def model(self) -> str:
        return os.environ.get(self.model_env, self.default_model) if self.model_env else ""


def _extract_json(texte: str) -> dict:
    """Lit l'objet JSON d'une réponse, même entourée de bavardage.

    Les modèles gratuits encadrent souvent leur JSON d'un bloc de code ou d'une
    phrase d'introduction, malgré la consigne. Le refuser pour cette raison
    gaspillerait un appel qui a pourtant abouti.
    """
    texte = texte.strip()
    if texte.startswith("```"):
        texte = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", texte).strip()
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        pass
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut >= 0 and fin > debut:
        try:
            return json.loads(texte[debut : fin + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"réponse non analysable en JSON : {exc}") from exc
    raise ProviderError("réponse sans objet JSON")


def _post(url: str, headers: dict, payload: dict) -> dict:
    reponse = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    if reponse.status_code != 200:
        message = f"HTTP {reponse.status_code}: {reponse.text[:300]}"
        if reponse.status_code in RETRYABLE_STATUS:
            raise ProviderError(message)
        # Une clé invalide ou un modèle inexistant ne se répare pas en
        # réessayant : on passe au fournisseur suivant sans attendre.
        raise ProviderError(f"[definitif] {message}")
    return reponse.json()


def call_openai_compatible(
    base_url: str, api_key: str, model: str, system: str, user: str, schema: dict
) -> dict:
    """Appelle une API au format « chat completions », le plus répandu."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "extraction", "schema": schema, "strict": False},
        },
    }
    data = _post(base_url, {"Authorization": f"Bearer {api_key}"}, payload)
    try:
        contenu = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"réponse de forme inattendue : {str(data)[:200]}") from exc
    return _extract_json(contenu)


def call_gemini(api_key: str, model: str, system: str, user: str, schema: dict) -> dict:
    """Appelle l'API Gemini, qui a sa propre forme de requête."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": f"{system}\n\n{user}"}]}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    data = _post(url, {}, payload)
    try:
        contenu = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise ProviderError(f"réponse de forme inattendue : {str(data)[:200]}") from exc
    return _extract_json(contenu)


def _openai_like(base_url: str, env_key: str, model_env: str, default_model: str, name: str) -> Provider:
    def call(system: str, user: str, schema: dict, model: str) -> dict:
        return call_openai_compatible(
            base_url, os.environ[env_key], model, system, user, schema
        )

    return Provider(name=name, env_key=env_key, call=call,
                    model_env=model_env, default_model=default_model)


def default_providers() -> list[Provider]:
    """Fournisseurs tentés dans l'ordre, les paliers gratuits d'abord.

    Les modèles par défaut sont ceux des offres gratuites au moment de
    l'écriture ; ils bougent, donc chacun est surchargeable par une variable
    d'environnement sans toucher au code.
    """
    providers = [
        _openai_like(
            "https://api.groq.com/openai/v1/chat/completions",
            "GROQ_API_KEY", "GROQ_MODEL", "llama-3.3-70b-versatile", "groq",
        ),
        _openai_like(
            "https://openrouter.ai/api/v1/chat/completions",
            "OPENROUTER_API_KEY", "OPENROUTER_FREE_MODEL",
            "meta-llama/llama-3.3-70b-instruct:free", "openrouter-gratuit",
        ),
        Provider(
            name="gemini",
            env_key="GEMINI_API_KEY",
            model_env="GEMINI_MODEL",
            default_model="gemini-2.0-flash",
            call=lambda system, user, schema, model: call_gemini(
                os.environ["GEMINI_API_KEY"], model, system, user, schema
            ),
        ),
        _openai_like(
            "https://api.cerebras.ai/v1/chat/completions",
            "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "llama-3.3-70b", "cerebras",
        ),
        _openai_like(
            "https://api.mistral.ai/v1/chat/completions",
            "MISTRAL_API_KEY", "MISTRAL_MODEL", "mistral-small-latest", "mistral",
        ),
        # Palier payant, tenté en dernier et seulement si une clé existe.
        _openai_like(
            "https://openrouter.ai/api/v1/chat/completions",
            "OPENROUTER_API_KEY", "OPENROUTER_PAID_MODEL", "", "openrouter-payant",
        ),
    ]
    return providers


@dataclass
class CallResult:
    """Ce qu'a rendu l'appel, et ce qu'il a coûté en tentatives."""

    data: dict
    provider: str
    model: str
    attempts: int = 1
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "errors": self.errors,
        }


def ask(
    system: str,
    user: str,
    schema: dict,
    providers: list[Provider] | None = None,
    max_retries: int = MAX_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> CallResult:
    """Interroge les fournisseurs dans l'ordre jusqu'à obtenir une réponse.

    ``sleep`` est injectable pour que les tests n'attendent pas réellement.
    """
    providers = providers if providers is not None else default_providers()
    disponibles = [p for p in providers if p.available]
    if not disponibles:
        raise ProviderError(
            "aucun fournisseur configuré : renseignez au moins une des variables "
            + ", ".join(sorted({p.env_key for p in providers}))
        )

    erreurs: list[str] = []
    tentatives = 0
    for provider in disponibles:
        modele = provider.model
        if provider.model_env and not modele:
            # Palier payant sans modèle choisi : on ne devine pas un modèle
            # facturé à la place de l'utilisateur.
            erreurs.append(f"{provider.name}: aucun modèle défini ({provider.model_env})")
            continue
        for essai in range(max_retries + 1):
            tentatives += 1
            try:
                data = provider.call(system, user, schema, modele)
                if not isinstance(data, dict):
                    raise ProviderError(f"réponse de type {type(data).__name__}, objet attendu")
                return CallResult(data=data, provider=provider.name, model=modele,
                                  attempts=tentatives, errors=erreurs)
            except ProviderError as exc:
                erreurs.append(f"{provider.name}: {exc}")
                if "[definitif]" in str(exc) or essai == max_retries:
                    break
                sleep(BACKOFF_BASE ** essai)
            except Exception as exc:  # réseau, délai dépassé, JSON illisible
                erreurs.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                if essai == max_retries:
                    break
                sleep(BACKOFF_BASE ** essai)

    raise ProviderError("tous les fournisseurs ont échoué — " + " | ".join(erreurs[-4:]))
