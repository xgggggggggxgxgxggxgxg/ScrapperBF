"""
Banc d'essai des modèles LLM pour l'EXTRACTION d'avis (rôle extracteur).

Faute de vérité terrain, on mesure des indicateurs PROXY, calculables sans
étiquetage, sur de VRAIES annonces :
  - % JSON valide          : respect du format structuré
  - remplissage moyen      : champs clés (CORE_FIELDS) non vides
  - confiance du juge       : notre juge déterministe note chaque sortie
  - tokens in/out/total     : coût réel
  - latence / débit (t/s)   : vitesse
  - erreurs                 : robustesse

Usage :
    python bench_models.py bulletin.pdf \
        --models "google/gemma-4-26b-it:free, thinkingmachines/inkling:free" \
        --n 8

Le cœur (evaluate_models) est PUR et testable hors-ligne avec un appelant injecté.
La vraie exécution appelle OpenRouter (ou Gemini / endpoint compatible OpenAI).

OPTION vérité terrain : --gold gold.json  (dict {index: {champ: valeur_attendue}})
calcule en plus l'exactitude par champ (exact match, insensible casse/espaces).
"""

import re
import sys
import json
import time
import logging
import argparse
from pathlib import Path
from typing import Optional, Callable

from layer3_extract_llm import (
    load_field_schema, build_json_schema,
    call_openrouter, call_gemini, call_openai_compatible,
)
from consensus_extract import EXTRACT_SYSTEM
from pdf_avis_extract import CORE_FIELDS
from judge import judge_avis, fold
import config
import token_tracker

log = logging.getLogger("scrapperbf.bench")


def _prompt(text: str, schema: dict) -> str:
    return (f"Schéma attendu (informatif) :\n{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Texte de l'annonce :\n{text}")


def real_caller(spec: dict, text: str, schema: dict):
    """Appelle UN modèle une fois. Retourne (result|None, usage|None, latence_s, err)."""
    p = _prompt(text, schema)
    t0 = time.perf_counter()
    try:
        if spec["kind"] == "openrouter":
            res = call_openrouter(p, schema, spec["key"], spec["model"],
                                  system_prompt=EXTRACT_SYSTEM, strict=False)
        elif spec["kind"] == "gemini":
            res = call_gemini(p, schema, spec["key"], model=spec["model"],
                              system_prompt=EXTRACT_SYSTEM, strict=False)
        else:  # openai_compat
            res = call_openai_compatible(p, schema, spec["key"], spec["base_url"],
                                         spec["model"], system_prompt=EXTRACT_SYSTEM, strict=False)
    except Exception as e:
        return None, None, time.perf_counter() - t0, str(e)[:140]
    dt = time.perf_counter() - t0
    usage = res.pop("_usage", None) if isinstance(res, dict) else None
    if usage:
        token_tracker.record(spec["kind"], spec["model"], spec["key"], usage)
    return res, usage, dt, None


def _avg(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def _match(a, b) -> bool:
    return fold(a) == fold(b) and bool(fold(a))


def evaluate_models(texts: list, specs: list, caller: Callable = real_caller,
                    schema: Optional[dict] = None, gold: Optional[dict] = None) -> dict:
    """Évalue chaque modèle sur chaque texte. Retourne {nom_modele: stats}. PUR :
    `caller(spec, text, schema)->(res,usage,latence,err)` est injectable."""
    schema = schema or build_json_schema(load_field_schema("AVIS"))
    out = {}
    for spec in specs:
        appels = ok = err = 0
        fill, conf, lat = [], [], []
        tin = tout = ttot = 0
        gold_hits = gold_tot = 0
        for i, text in enumerate(texts):
            res, usage, dt, e = caller(spec, text, schema)
            appels += 1
            lat.append(dt)
            if e or not isinstance(res, dict):
                err += 1
                continue
            ok += 1
            fill.append(sum(1 for f in CORE_FIELDS if res.get(f)) / len(CORE_FIELDS))
            conf.append(judge_avis(res, text).get("confiance", 0.0))
            if usage:
                tin += usage.get("prompt", 0); tout += usage.get("completion", 0)
                ttot += usage.get("total", 0)
            if gold and str(i) in gold:
                for champ, attendu in gold[str(i)].items():
                    gold_tot += 1
                    gold_hits += 1 if _match(res.get(champ), attendu) else 0
        debit = round(tout / sum(lat), 1) if tout and sum(lat) else None
        out[spec["name"]] = {
            "appels": appels, "json_valide": ok, "erreurs": err,
            "taux_json_valide": round(ok / appels, 3) if appels else None,
            "remplissage_moyen": _avg(fill),
            "confiance_juge_moyenne": _avg(conf),
            "tokens_in": tin, "tokens_out": tout, "tokens_total": ttot,
            "tokens_moyen_par_annonce": round(ttot / ok) if ok else None,
            "latence_moyenne_s": _avg(lat), "debit_t_s": debit,
            "exactitude_gold": (round(gold_hits / gold_tot, 3) if gold_tot else None),
        }
    return out


def format_table(results: dict) -> str:
    cols = [("modèle", "{}"), ("ok", "{json_valide}/{appels}"),
            ("JSON%", "{taux_json_valide}"), ("rempl.", "{remplissage_moyen}"),
            ("juge", "{confiance_juge_moyenne}"), ("tok/ann", "{tokens_moyen_par_annonce}"),
            ("tok tot", "{tokens_total}"), ("lat s", "{latence_moyenne_s}"),
            ("t/s", "{debit_t_s}"), ("gold", "{exactitude_gold}")]
    rows = [[c[0] for c in cols]]
    for name, s in results.items():
        rows.append([name] + [c[1].format(**s) if c[0] != "modèle" else name for c in cols[1:]])
    w = [max(len(str(r[i])) for r in rows) for i in range(len(cols))]
    def line(r): return "  ".join(str(r[i]).ljust(w[i]) if i == 0 else str(r[i]).rjust(w[i]) for i in range(len(cols)))
    return "\n".join([line(rows[0]), "  ".join("-" * w[i] for i in range(len(cols)))] + [line(r) for r in rows[1:]])


def _specs_from_models(models: list, provider: str) -> list:
    """Construit les specs à partir d'une liste d'ID de modèles (même provider)."""
    if provider == "openrouter":
        keys = config._keys("OPENROUTER")
        if not keys:
            sys.exit("Aucune clé OpenRouter (OPENROUTER_API_KEYS dans .env).")
        return [{"name": m, "kind": "openrouter", "key": keys[0], "model": m} for m in models]
    if provider == "gemini":
        keys = config._keys("GEMINI")
        if not keys:
            sys.exit("Aucune clé Gemini.")
        return [{"name": m, "kind": "gemini", "key": keys[0], "model": m} for m in models]
    base = config.os.environ.get("OPENAI_COMPAT_BASE_URL", "")
    keys = config._keys("OPENAI_COMPAT") or ["sk-local"]
    return [{"name": m, "kind": "openai_compat", "key": keys[0], "base_url": base, "model": m} for m in models]


def main():
    from logging_config import setup_logging
    setup_logging()
    ap = argparse.ArgumentParser(description="Banc d'essai des modèles d'extraction")
    ap.add_argument("pdf", help="PDF de bulletin (ses annonces servent d'échantillon)")
    ap.add_argument("--models", required=True, help="IDs séparés par des virgules")
    ap.add_argument("--provider", default="openrouter", choices=["openrouter", "gemini", "openai_compat"])
    ap.add_argument("--n", type=int, default=6, help="nombre d'annonces testées")
    ap.add_argument("--gold", help="JSON de vérité terrain (optionnel)")
    ap.add_argument("--out", default="bench_results.json")
    args = ap.parse_args()

    from pdf_avis_extract import segment_avis_blocks
    from section_split import split_sections
    zones = split_sections(args.pdf)
    blocks = segment_avis_blocks(args.pdf, page_range=zones["AVIS"])
    texts = [b["flat_text"] for b in blocks if b.get("flat_text")][:args.n]
    if not texts:
        sys.exit("Aucune annonce extraite du PDF.")

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    specs = _specs_from_models(models, args.provider)
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8")) if args.gold else None

    log.info("Banc d'essai : %d modèle(s) × %d annonce(s)", len(specs), len(texts))
    results = evaluate_models(texts, specs, gold=gold)
    print("\n" + format_table(results) + "\n")
    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Détail -> %s | tokens cumulés :\n%s", args.out, token_tracker.report())


if __name__ == "__main__":
    main()
