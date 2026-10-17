"""
Vérificateur de clés OpenRouter — teste CHAQUE clé/modèle configuré par un
mini-appel (1 token) et dit si elle marche. Sert à diagnostiquer les 401
(« User not found » = clé invalide), 404 (slug de modèle inexistant), etc.

Usage :
    python check_keys.py                 # teste model_keys.json + OPENROUTER_MODELS
    python check_keys.py --models "a,b"  # teste des slugs précis

Lit les clés comme le reste du projet (model_keys.json prioritaire, pool en repli).
"""

import sys
import logging
import argparse
from typing import Callable, Optional

import requests
import config
import token_tracker

log = logging.getLogger("scrapperbf.checkkeys")


def check_one(model: str, key: str, poster: Optional[Callable] = None) -> dict:
    """Mini-appel (max_tokens=1). Retourne {model, ok, status, detail, cle}."""
    poster = poster or requests.post
    cle = token_tracker.mask(key)
    try:
        r = poster("https://openrouter.ai/api/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                   json={"model": model, "max_tokens": 1,
                         "messages": [{"role": "user", "content": "ping"}]},
                   timeout=30)
    except Exception as e:
        return {"model": model, "ok": False, "status": "réseau", "detail": str(e)[:120], "cle": cle}
    ok = r.status_code == 200
    detail = "" if ok else str(getattr(r, "text", ""))[:160]
    return {"model": model, "ok": ok, "status": r.status_code, "detail": detail, "cle": cle}


def diagnose(status, detail: str) -> str:
    s = f"{status} {detail}".lower()
    if status == 401 or "user not found" in s or "no auth" in s or "invalid api key" in s:
        return "CLÉ INVALIDE (régénérée ? tronquée ? placeholder ?)"
    if status == 404 or "no endpoints" in s or "not found for" in s:
        return "SLUG DE MODÈLE INEXISTANT (vérifie l'ID exact sur openrouter.ai)"
    if status == 429 or "rate" in s or "quota" in s:
        return "QUOTA/LIMITE (clé OK, réessaie plus tard ou ajoute une clé)"
    if status == 200:
        return "OK"
    return "autre erreur"


def check_all(pairs: list, poster: Optional[Callable] = None) -> list:
    """pairs = [(model, key)]. Retourne la liste des diagnostics."""
    out = []
    for model, key in pairs:
        r = check_one(model, key, poster)
        r["diagnostic"] = diagnose(r["status"], r["detail"])
        out.append(r)
    return out


def _pairs_from_config(models: Optional[list]) -> list:
    mk = config.model_key_map()
    pool = config._keys("OPENROUTER")
    if not models:
        models = sorted(set(list(mk.keys()) + config._split_models(
            config.os.environ.get("OPENROUTER_MODELS", ""), "")))
    pairs = []
    for m in models:
        keys = config.keys_for_model(m, pool, mk)
        pairs.append((m, keys[0] if keys else ""))
    return pairs


def main():
    from logging_config import setup_logging
    setup_logging()
    ap = argparse.ArgumentParser(description="Vérifie les clés OpenRouter")
    ap.add_argument("--models", help="slugs séparés par des virgules (sinon ceux configurés)")
    args = ap.parse_args()
    models = [m.strip() for m in args.models.split(",")] if args.models else None
    pairs = _pairs_from_config(models)
    if not any(k for _, k in pairs):
        sys.exit("Aucune clé trouvée. Remplis model_keys.json ou OPENROUTER_API_KEYS.")

    print(f"{'modèle':52} {'clé':14} {'statut':8} diagnostic")
    print("-" * 100)
    nb_ok = 0
    for r in check_all(pairs):
        nb_ok += 1 if r["ok"] else 0
        print(f"{r['model']:52} {r['cle']:14} {str(r['status']):8} {r['diagnostic']}")
        if r["detail"] and not r["ok"]:
            print(f"    └─ {r['detail']}")
    print("-" * 100)
    print(f"{nb_ok}/{len(pairs)} clé(s) OK")


if __name__ == "__main__":
    main()
