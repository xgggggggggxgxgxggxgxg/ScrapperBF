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


def _keys(prefix: str) -> list:
    """Liste des clés d'un provider. Accepte PLUSIEURS clés via
    « {PREFIX}_API_KEYS » (séparées par des virgules) et/ou une clé unique
    « {PREFIX}_API_KEY ». Dédupliquées, ordre conservé."""
    raw = []
    multi = os.environ.get(f"{prefix}_API_KEYS", "")
    if multi:
        raw += [k.strip() for k in multi.split(",")]
    single = os.environ.get(f"{prefix}_API_KEY", "").strip()
    if single:
        raw.append(single)
    seen, out = set(), []
    for k in raw:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def provider_specs() -> list:
    """Décrit les providers CONFIGURÉS (clé présente), prêts à être activés.
    Chaque spec : {name, kind, key, keys, model, base_url?} — `keys` est la liste
    des clés à essayer (rotation en cas de quota/erreur), `key` = la 1re (compat).
    L'ordre = ordre d'essai."""
    specs = []

    ork = _keys("OPENROUTER")
    if ork:
        models = _split_models(
            os.environ.get("OPENROUTER_MODELS", ""),
            f"{os.environ.get('OPENROUTER_XIAOMI_MODEL', 'xiaomi/mimo-v2.5')},"
            f"{os.environ.get('OPENROUTER_MISTRAL_MODEL', 'mistralai/mistral-large')}",
        )
        for model in models:
            specs.append({"name": f"openrouter:{model}", "kind": "openrouter",
                          "key": ork[0], "keys": ork, "model": model})

    gk = _keys("GEMINI")
    if gk:
        specs.append({"name": "gemini", "kind": "gemini", "key": gk[0], "keys": gk,
                      "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")})

    ock = _keys("OPENAI_COMPAT")
    ocb = os.environ.get("OPENAI_COMPAT_BASE_URL", "").strip()
    if ocb:  # une base_url suffit (certains serveurs locaux n'exigent pas de clé)
        keys = ock or ["sk-local"]
        specs.append({"name": "openai-compat", "kind": "openai_compat",
                      "key": keys[0], "keys": keys, "base_url": ocb,
                      "model": os.environ.get("OPENAI_COMPAT_MODEL", "gpt-4o-mini")})

    return specs


def is_configured() -> bool:
    return bool(provider_specs())


def span_spec():
    """Spec du scoreur comportemental optionnel (Span-01), ou None si pas de clé.
    Compatible endpoint OpenAI (OpenRouter par défaut)."""
    keys = _keys("SPAN")
    if not keys:
        return None
    return {"kind": "span", "key": keys[0], "keys": keys,
            "model": os.environ.get("SPAN_MODEL", "respan/span-01-lite:free"),
            "base_url": os.environ.get("SPAN_BASE_URL", "https://openrouter.ai/api/v1")}


def summary() -> str:
    specs = provider_specs()
    if not specs:
        return ("Aucun provider LLM configuré. Copie .env.example -> .env et "
                "renseigne au moins une clé (OPENROUTER_API_KEY, GEMINI_API_KEY, "
                "ou OPENAI_COMPAT_BASE_URL).")
    lines = [f"{len(specs)} provider(s) configuré(s) (ordre d'essai) :"]
    for i, s in enumerate(specs, 1):
        extra = f" @ {s['base_url']}" if s.get("base_url") else ""
        nk = len(s.get("keys") or [s.get("key")])
        klab = f" — {nk} clé(s)" if nk > 1 else ""
        lines.append(f"  {i}. {s['name']}  [modèle: {s['model']}{extra}]{klab}")
    return "\n".join(lines)
