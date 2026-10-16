"""
Comptage des TOKENS consommés par les API LLM (et OCR), par provider / modèle /
clé. Persistant (JSON), thread-safe, clés masquées. Sert à :
  - savoir exactement combien de tokens chaque clé a consommés ;
  - repérer une clé qui accumule des erreurs (quota) pour la rotation.

Usage : token_tracker.record(kind, model, key, usage) après chaque appel réussi ;
token_tracker.record_error(kind, model, key, raison) sur échec. `report()` rend un
tableau lisible. CLI : `python token_tracker.py` (affiche), `--reset` (remet à 0).
"""

import os
import json
import logging
import threading
from pathlib import Path

log = logging.getLogger("scrapperbf.tokens")

STORE = Path(os.environ.get("TOKEN_USAGE_FILE", Path(__file__).parent / "token_usage.json"))
_lock = threading.Lock()


def mask(key) -> str:
    k = str(key or "")
    if len(k) <= 8:
        return k or "—"
    return f"{k[:4]}…{k[-4:]}"


def _load() -> dict:
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": {}, "total_tokens": 0}


def _save(data: dict) -> None:
    try:
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("écriture %s impossible : %s", STORE, e)


def normalize_usage(kind: str, raw: dict) -> dict:
    """Ramène les champs d'usage des différents formats à
    {prompt, completion, total}."""
    raw = raw or {}
    if kind == "gemini":
        p = raw.get("promptTokenCount", 0) or 0
        c = raw.get("candidatesTokenCount", 0) or 0
        t = raw.get("totalTokenCount", 0) or (p + c)
    else:  # openai / openrouter / openai_compat
        p = raw.get("prompt_tokens", 0) or 0
        c = raw.get("completion_tokens", 0) or 0
        t = raw.get("total_tokens", 0) or (p + c)
    return {"prompt": int(p), "completion": int(c), "total": int(t)}


def _key(kind, model, key) -> str:
    return f"{kind}|{model}|{mask(key)}"


def record(kind: str, model: str, key, usage: dict) -> None:
    """Enregistre les tokens d'un appel RÉUSSI (usage déjà normalisé {prompt,
    completion, total})."""
    if not usage:
        return
    with _lock:
        data = _load()
        e = data["entries"].setdefault(_key(kind, model, key),
                                       {"appels": 0, "prompt": 0, "completion": 0,
                                        "total": 0, "erreurs": 0})
        e["appels"] += 1
        e["prompt"] += usage.get("prompt", 0)
        e["completion"] += usage.get("completion", 0)
        e["total"] += usage.get("total", 0)
        data["total_tokens"] = sum(v["total"] for v in data["entries"].values())
        _save(data)


def record_error(kind: str, model: str, key, raison: str = "") -> None:
    """Incrémente le compteur d'erreurs d'une clé (quota, réseau, 429…)."""
    with _lock:
        data = _load()
        e = data["entries"].setdefault(_key(kind, model, key),
                                       {"appels": 0, "prompt": 0, "completion": 0,
                                        "total": 0, "erreurs": 0})
        e["erreurs"] += 1
        _save(data)


def snapshot() -> dict:
    with _lock:
        return _load()


def reset() -> None:
    with _lock:
        _save({"entries": {}, "total_tokens": 0})


def report() -> str:
    data = _load()
    entries = data.get("entries", {})
    if not entries:
        return "Aucune consommation enregistrée (token_usage.json vide)."
    rows = [("provider | modèle | clé", "appels", "prompt", "compl.", "total", "err")]
    for k, v in sorted(entries.items(), key=lambda kv: -kv[1]["total"]):
        rows.append((k, str(v["appels"]), str(v["prompt"]), str(v["completion"]),
                     str(v["total"]), str(v["erreurs"])))
    w = [max(len(r[i]) for r in rows) for i in range(6)]
    def fmt(r): return "  ".join(r[i].ljust(w[i]) if i == 0 else r[i].rjust(w[i]) for i in range(6))
    out = [fmt(rows[0]), "  ".join("-" * w[i] for i in range(6))]
    out += [fmt(r) for r in rows[1:]]
    out.append(f"\nTOTAL tokens : {data.get('total_tokens', 0):,}".replace(",", " "))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if "--reset" in sys.argv:
        reset()
        print("Compteurs remis à zéro.")
    else:
        print(report())
