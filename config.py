"""
Configuration centralisée des API LLM — pour brancher TES clés chez toi.

Mise en route :
    cp .env.example .env     # puis renseigne tes clés dans .env
Le .env est chargé automatiquement ici (aucune dépendance externe). Un provider
n'est ACTIVÉ que si sa clé est présente : tu peux donc n'en configurer qu'un seul.

Providers supportés :
  - OpenRouter (n'importe quel modèle du catalogue) : OPENROUTER_API_KEY (+ OPENROUTER_MODELS)
  - Google Gemini                                   : GEMINI_API_KEY (+ GEMINI_MODEL)
  - Tout endpoint compatible OpenAI (local, vLLM,    : OPENAI_COMPAT_BASE_URL
    Groq, Together, Ollama, LM Studio…)               + OPENAI_COMPAT_API_KEY (+ OPENAI_COMPAT_MODEL)

L'ordre d'essai (rotation / repli) suit l'ordre ci-dessous ; il sert aussi de
répartition de charge entre fournisseurs ayant chacun leur quota.
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).parent / ".env"


def load_dotenv(path=None) -> None:
    """Charge un fichier .env (KEY=VALUE par ligne) dans os.environ, sans écraser
    une variable déjà définie dans l'environnement. Tolérant : commentaires (#),
    guillemets, lignes vides. Aucune dépendance (pas besoin de python-dotenv)."""
    p = Path(path or ENV_PATH)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


# Charge .env dès l'import de ce module (donc avant la construction des providers).
load_dotenv()


def _split_models(value: str, default: str) -> list:
    raw = value or default
    return [m.strip() for m in raw.split(",") if m.strip()]


def provider_specs() -> list:
    """Décrit les providers CONFIGURÉS (clé présente), prêts à être activés.
    Chaque spec : {name, kind, key, model, base_url?}. L'ordre = ordre d'essai."""
    specs = []

    ork = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if ork:
        models = _split_models(
            os.environ.get("OPENROUTER_MODELS", ""),
            # Repli historique du projet : modèle perso puis Mistral.
            f"{os.environ.get('OPENROUTER_XIAOMI_MODEL', 'xiaomi/mimo-v2.5')},"
            f"{os.environ.get('OPENROUTER_MISTRAL_MODEL', 'mistralai/mistral-large')}",
        )
        for i, model in enumerate(models):
            specs.append({"name": f"openrouter:{model}", "kind": "openrouter",
                          "key": ork, "model": model})

    gk = os.environ.get("GEMINI_API_KEY", "").strip()
    if gk:
        specs.append({"name": "gemini", "kind": "gemini", "key": gk,
                      "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")})

    ock = os.environ.get("OPENAI_COMPAT_API_KEY", "").strip()
    ocb = os.environ.get("OPENAI_COMPAT_BASE_URL", "").strip()
    if ocb:  # une base_url suffit (certains serveurs locaux n'exigent pas de clé)
        specs.append({"name": "openai-compat", "kind": "openai_compat",
                      "key": ock or "sk-local", "base_url": ocb,
                      "model": os.environ.get("OPENAI_COMPAT_MODEL", "gpt-4o-mini")})

    return specs


def is_configured() -> bool:
    return bool(provider_specs())


def summary() -> str:
    specs = provider_specs()
    if not specs:
        return ("Aucun provider LLM configuré. Copie .env.example -> .env et "
                "renseigne au moins une clé (OPENROUTER_API_KEY, GEMINI_API_KEY, "
                "ou OPENAI_COMPAT_BASE_URL).")
    lines = [f"{len(specs)} provider(s) configuré(s) (ordre d'essai) :"]
    for i, s in enumerate(specs, 1):
        extra = f" @ {s['base_url']}" if s.get("base_url") else ""
        lines.append(f"  {i}. {s['name']}  [modèle: {s['model']}{extra}]")
    return "\n".join(lines)
