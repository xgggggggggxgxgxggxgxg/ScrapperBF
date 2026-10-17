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
import os
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
from consensus_extract import EXTRACT_SYSTEM, ARBITER_SYSTEM
from pdf_avis_extract import CORE_FIELDS
from judge import judge_avis, fold
import span_scorer
import config
import token_tracker

log = logging.getLogger("scrapperbf.bench")

# Roster de l'utilisateur : les modèles candidats par RÔLE (ordre de priorité).
ROLE_PRESETS = {
    "extracteur": [
        "google/gemma-4-26b-a4b-it:free",
        "google/gemma-4-31b-it:free",
        "nvidia/nemotron-3.5-lightning:free",
        "liquid/lfm-2.5-2.6b:free",
        "thinkingmachines/inkling-small:free",
    ],
    "juge": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "thinkingmachines/inkling:free",
    ],
    "scoreur": [
        "respan/span-01-lite:free",
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    ],
}

# Schéma de sortie de l'arbitre (identique à celui de la production).
ARB_SCHEMA = {
    "type": "object",
    "properties": {
        "champs": {"type": "object"},
        "desaccords": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["champs"],
}


def _prompt(text: str, schema: dict) -> str:
    return (f"Schéma attendu (informatif) :\n{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"Texte de l'annonce :\n{text}")


# Tier gratuit = débit limité : on retente sur 429 avec pause croissante.
BENCH_RETRY_429 = int(os.environ.get("BENCH_RETRY_429", "3"))
BENCH_BACKOFF = float(os.environ.get("BENCH_BACKOFF", "6"))

# Affichage détaillé de la progression (mis à True par --verbose).
VERBOSE = os.environ.get("BENCH_VERBOSE", "0") not in ("0", "", "false", "False")


def _vlog(msg, *args):
    if VERBOSE:
        log.info(msg, *args)


def _is_429(e) -> bool:
    s = str(e).lower()
    return "429" in s or "rate" in s or "temporarily" in s or "quota" in s


def _systemic(e) -> bool:
    """Erreur qui touchera TOUTES les annonces de ce modèle (inutile d'insister) :
    saturation (429), clé (401), slug (404). On abandonne le modèle pour gagner
    du temps au lieu de refaire toutes les tentatives."""
    s = str(e).lower()
    return any(x in s for x in ("429", "rate", "temporarily", "quota",
                                "401", "user not found", "no auth",
                                "404", "no endpoints", "not found for"))


def _call_retry(do_call: Callable):
    """Exécute do_call() (qui lève en cas d'erreur). Retente sur 429 avec backoff.
    Retourne (res|None, latence_s, err|None)."""
    last = ""
    for attempt in range(BENCH_RETRY_429):
        t0 = time.perf_counter()
        try:
            return do_call(), time.perf_counter() - t0, None
        except Exception as e:
            last = str(e)[:160]
            if _is_429(e) and attempt < BENCH_RETRY_429 - 1:
                pause = BENCH_BACKOFF * (2 ** attempt)
                log.info("429 (débit limité), pause %.0fs puis nouvelle tentative…", pause)
                time.sleep(pause)
                continue
            return None, time.perf_counter() - t0, last
    return None, 0.0, last


def _dispatch(spec: dict, prompt: str, schema: dict, system: str):
    if spec["kind"] == "gemini":
        return call_gemini(prompt, schema, spec["key"], model=spec["model"],
                           system_prompt=system, strict=False)
    if spec["kind"] == "openai_compat":
        return call_openai_compatible(prompt, schema, spec["key"], spec["base_url"],
                                      spec["model"], system_prompt=system, strict=False)
    return call_openrouter(prompt, schema, spec["key"], spec["model"],
                           system_prompt=system, strict=False)


def _finish(spec, res, dt, err):
    if err:
        return None, None, dt, err
    usage = res.pop("_usage", None) if isinstance(res, dict) else None
    if usage:
        token_tracker.record(spec["kind"], spec["model"], spec["key"], usage)
    return res, usage, dt, None


def real_caller(spec: dict, text: str, schema: dict):
    """Appelle UN modèle (retry sur 429). Retourne (result|None, usage|None, latence, err)."""
    res, dt, err = _call_retry(lambda: _dispatch(spec, _prompt(text, schema), schema, EXTRACT_SYSTEM))
    return _finish(spec, res, dt, err)


def arbiter_caller(spec: dict, text: str, a: dict, b: dict):
    """Appelle UN modèle arbitre (texte + extraction A + B). Retourne
    (result|None, usage|None, latence_s, err). result attendu : {champs, desaccords}."""
    prompt = (f"Texte de l'annonce :\n{text}\n\n"
              f"Extraction A :\n{json.dumps(a, ensure_ascii=False)}\n\n"
              f"Extraction B :\n{json.dumps(b, ensure_ascii=False)}\n\n"
              f"Schéma de sortie (informatif) :\n{json.dumps(ARB_SCHEMA, ensure_ascii=False)}")
    res, dt, err = _call_retry(lambda: call_openrouter(
        prompt, ARB_SCHEMA, spec["key"], spec["model"], system_prompt=ARBITER_SYSTEM, strict=False))
    return _finish(spec, res, dt, err)


def span_caller(spec: dict, text: str, behaviors: list):
    """Appelle UN modèle scoreur. Retourne ({comportement: proba}|None, usage, latence, err)."""
    prompt = ("Comportements :\n" + "\n".join(f"- {bb}" for bb in behaviors)
              + f"\n\nTexte de l'annonce :\n{text}")
    res, dt, err = _call_retry(lambda: call_openrouter(
        prompt, {}, spec["key"], spec["model"], system_prompt=span_scorer.SPAN_SYSTEM, strict=False))
    return _finish(spec, res, dt, err)


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
        err_msg = None
        _vlog("▶ extracteur %s : %d annonce(s)", spec["name"], len(texts))
        for i, text in enumerate(texts):
            _vlog("  · %s — annonce %d/%d…", spec["name"], i + 1, len(texts))
            res, usage, dt, e = caller(spec, text, schema)
            appels += 1
            lat.append(dt)
            if e or not isinstance(res, dict):
                err += 1
                err_msg = err_msg or e or "réponse non-dict"
                _vlog("    ✗ %.1fs — %s", dt, (e or "non-dict"))
                if _systemic(e):
                    log.warning("[%s] erreur systémique (saturation/clé/slug) -> modèle abandonné", spec["name"])
                    break
                continue
            ok += 1
            _vlog("    ✓ %.1fs", dt)
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
            "erreur_exemple": err_msg,
        }
        if err_msg:
            log.warning("[%s] %d/%d échecs — exemple : %s", spec["name"], err, appels, err_msg)
    return out


def evaluate_juges(texts: list, specs: list, ab_pairs: list,
                   caller: Callable = arbiter_caller) -> dict:
    """Évalue des ARBITRES : chacun reçoit (texte, A, B) et doit trancher.
    `ab_pairs[i] = (A, B)`. Mesure validité JSON, remplissage, confiance du juge
    sur les champs arbitrés, et le TAUX D'EXPLICATION des divergences (A≠B ->
    `desaccords` non vide). PUR (caller injectable)."""
    out = {}
    for spec in specs:
        appels = ok = err = 0
        fill, conf, lat = [], [], []
        ttot = 0
        expl_ok = expl_tot = 0
        err_msg = None
        _vlog("▶ arbitre %s : %d annonce(s)", spec["name"], len(texts))
        for i, ((a, b), text) in enumerate(zip(ab_pairs, texts)):
            _vlog("  · %s — annonce %d/%d…", spec["name"], i + 1, len(texts))
            res, usage, dt, e = caller(spec, text, a, b)
            appels += 1
            lat.append(dt)
            champs = res.get("champs") if isinstance(res, dict) else None
            if e or not isinstance(champs, dict):
                err += 1
                err_msg = err_msg or e or "pas de champ 'champs'"
                _vlog("    ✗ %.1fs — %s", dt, (e or "pas de 'champs'"))
                if _systemic(e):
                    log.warning("[%s] erreur systémique -> modèle abandonné", spec["name"])
                    break
                continue
            ok += 1
            _vlog("    ✓ %.1fs", dt)
            fill.append(sum(1 for f in CORE_FIELDS if champs.get(f)) / len(CORE_FIELDS))
            conf.append(judge_avis(champs, text).get("confiance", 0.0))
            if usage:
                ttot += usage.get("total", 0)
            # A et B diffèrent-ils ? si oui, l'arbitre doit l'expliquer.
            if fold(json.dumps(a, ensure_ascii=False)) != fold(json.dumps(b, ensure_ascii=False)):
                expl_tot += 1
                if res.get("desaccords"):
                    expl_ok += 1
        out[spec["name"]] = {
            "appels": appels, "json_valide": ok, "erreurs": err,
            "taux_json_valide": round(ok / appels, 3) if appels else None,
            "remplissage_moyen": _avg(fill),
            "confiance_juge_moyenne": _avg(conf),
            "explique_divergence": (round(expl_ok / expl_tot, 3) if expl_tot else None),
            "tokens_total": ttot,
            "tokens_moyen_par_annonce": round(ttot / ok) if ok else None,
            "latence_moyenne_s": _avg(lat),
            "erreur_exemple": err_msg,
        }
        if err_msg:
            log.warning("[%s] %d/%d échecs — exemple : %s", spec["name"], err, appels, err_msg)
    return out


def evaluate_scoreurs(texts: list, specs: list, behaviors: Optional[list] = None,
                      caller: Callable = span_caller) -> dict:
    """Évalue des SCOREURS comportementaux. Mesure fiabilité (JSON valide),
    COUVERTURE (part des comportements notés par un nombre), coût et vitesse.
    ⚠️ La CALIBRATION (proba ≈ réalité) exige une vérité terrain : non mesurée ici."""
    behaviors = behaviors or span_scorer.DEFAULT_BEHAVIORS
    out = {}
    for spec in specs:
        appels = ok = err = 0
        cover, lat = [], []
        ttot = 0
        err_msg = None
        _vlog("▶ scoreur %s : %d annonce(s)", spec["name"], len(texts))
        for i, text in enumerate(texts):
            _vlog("  · %s — annonce %d/%d…", spec["name"], i + 1, len(texts))
            res, usage, dt, e = caller(spec, text, behaviors)
            appels += 1
            lat.append(dt)
            if e or not isinstance(res, dict):
                err += 1
                err_msg = err_msg or e or "réponse non-dict"
                _vlog("    ✗ %.1fs — %s", dt, (e or "non-dict"))
                if _systemic(e):
                    log.warning("[%s] erreur systémique -> modèle abandonné", spec["name"])
                    break
                continue
            ok += 1
            _vlog("    ✓ %.1fs", dt)
            notes = sum(1 for bb in behaviors if isinstance(res.get(bb), (int, float)))
            cover.append(notes / len(behaviors))
            if usage:
                ttot += usage.get("total", 0)
        out[spec["name"]] = {
            "appels": appels, "json_valide": ok, "erreurs": err,
            "taux_json_valide": round(ok / appels, 3) if appels else None,
            "couverture_moyenne": _avg(cover),
            "tokens_total": ttot,
            "tokens_moyen_par_annonce": round(ttot / ok) if ok else None,
            "latence_moyenne_s": _avg(lat),
            "erreur_exemple": err_msg,
        }
        if err_msg:
            log.warning("[%s] %d/%d échecs — exemple : %s", spec["name"], err, appels, err_msg)
    return out


# Colonnes affichées par rôle (titre, clé de la métrique).
COLS = {
    "extracteur": [("modèle", None), ("ok", "json_valide"), ("JSON%", "taux_json_valide"),
                   ("rempl.", "remplissage_moyen"), ("juge", "confiance_juge_moyenne"),
                   ("tok/ann", "tokens_moyen_par_annonce"), ("tok tot", "tokens_total"),
                   ("lat s", "latence_moyenne_s"), ("t/s", "debit_t_s"), ("gold", "exactitude_gold")],
    "juge": [("modèle", None), ("ok", "json_valide"), ("JSON%", "taux_json_valide"),
             ("rempl.", "remplissage_moyen"), ("juge", "confiance_juge_moyenne"),
             ("explique", "explique_divergence"), ("tok/ann", "tokens_moyen_par_annonce"),
             ("lat s", "latence_moyenne_s")],
    "scoreur": [("modèle", None), ("ok", "json_valide"), ("JSON%", "taux_json_valide"),
                ("couverture", "couverture_moyenne"), ("tok/ann", "tokens_moyen_par_annonce"),
                ("lat s", "latence_moyenne_s")],
}


def format_table(results: dict, role: str = "extracteur") -> str:
    cols = COLS.get(role, COLS["extracteur"])
    rows = [[h for h, _ in cols]]
    for name, s in results.items():
        row = [name]
        for _, key in cols[1:]:
            row.append(str(s.get(key)))
        rows.append(row)
    w = [max(len(str(r[i])) for r in rows) for i in range(len(cols))]
    def line(r): return "  ".join(str(r[i]).ljust(w[i]) if i == 0 else str(r[i]).rjust(w[i]) for i in range(len(cols)))
    return "\n".join([line(rows[0]), "  ".join("-" * w[i] for i in range(len(cols)))] + [line(r) for r in rows[1:]])


# Métrique(s) de classement par rôle (domaine d'utilisation).
ROLE_METRIC = {
    "extracteur": ("confiance_juge_moyenne", "remplissage_moyen"),
    "juge": ("explique_divergence", "confiance_juge_moyenne"),
    "scoreur": ("couverture_moyenne",),
}
ROLE_LABEL = {
    "extracteur": "Extracteurs — domaine : extraction d'annonces en JSON (français)",
    "juge": "Arbitres — domaine : trancher entre A/B et EXPLIQUER les divergences",
    "scoreur": "Scoreurs — domaine : score comportemental (qualité souple)",
}


def rank_models(role: str, results: dict) -> list:
    """Classe les modèles d'un rôle, MEILLEUR d'abord : métrique(s) du domaine
    haute, puis moins d'erreurs, puis moins de tokens, puis plus rapide. PUR."""
    keys = ROLE_METRIC.get(role, ("taux_json_valide",))

    def score(s):
        prim = tuple((s.get(k) or 0) for k in keys)
        return prim + (-(s.get("erreurs") or 0),
                       -(s.get("tokens_moyen_par_annonce") or 10**9),
                       -(s.get("latence_moyenne_s") or 10**9))
    return [name for name, _ in sorted(results.items(), key=lambda kv: score(kv[1]), reverse=True)]


def build_report(results_by_role: dict, meta: dict, tokens: str = "") -> str:
    """Rapport Markdown consolidé : un tableau + un gagnant par domaine, puis la
    reco de production. PUR (testable)."""
    L = [f"# Rapport de banc d'essai — modèles LLM",
         "",
         f"- **Bulletin** : {meta.get('pdf', '?')}",
         f"- **Annonces testées** : {meta.get('n', '?')}",
         f"- **Date** : {meta.get('date', '?')}",
         ""]
    winners = {}
    for role in ("extracteur", "juge", "scoreur"):
        res = results_by_role.get(role)
        if not res:
            continue
        # On ne RECOMMANDE que des modèles qui ont réellement produit (json_valide>0).
        ranked = [m for m in rank_models(role, res) if (res[m].get("json_valide") or 0) > 0]
        winners[role] = ranked
        L += [f"## {ROLE_LABEL[role]}", "", "```", format_table(res, role), "```", ""]
        if ranked:
            L += [f"**Recommandé : `{ranked[0]}`**"
                  + (f" · 2ᵉ : `{ranked[1]}`" if len(ranked) > 1 else ""), ""]
        else:
            L += ["**Aucun modèle n'a abouti** (saturation/erreurs) — relancer plus "
                  "tard, baisser `--n`, ou ajouter du crédit OpenRouter.", ""]
    # Reco production — uniquement avec des modèles QUI ONT MARCHÉ.
    extr = winners.get("extracteur", [])
    arb = winners.get("juge", [])
    sco = winners.get("scoreur", [])
    trio = (extr[:2] + arb[:1]) if (len(extr) >= 2 and arb) else (extr + arb)[:3]
    L += ["## Recommandation de production", ""]
    if trio:
        note = "" if len(trio) >= 3 else \
            "  # ⚠️ peu de modèles disponibles : ajoute d'autres extracteurs/arbitres quand ils répondent"
        L += ["```bash", "# consensus = 2 extracteurs + 1 arbitre (ajoute-les dès qu'ils répondent)",
              "OPENROUTER_MODELS=" + ", ".join(trio) + note]
        if sco:
            L += ["ENABLE_SPAN_SCORING=1", f"SPAN_MODEL={sco[0]}"]
        L += ["```", ""]
    else:
        L += ["_Aucun modèle exploitable sur ce run._", ""]
    if tokens:
        L += ["## Consommation de tokens", "", "```", tokens, "```", ""]
    L += ["---", "_Indicateurs proxy (sans vérité terrain). Pour une exactitude "
          "absolue par champ, relancer l'extracteur avec `--gold`._"]
    return "\n".join(L)


def _specs_from_models(models: list, provider: str) -> list:
    """Construit les specs à partir d'une liste d'ID de modèles (même provider)."""
    if provider == "openrouter":
        pool = config._keys("OPENROUTER")
        mk = config.model_key_map()
        specs = []
        for m in models:
            keys = config.keys_for_model(m, pool, mk)  # clé dédiée du modèle + pool
            if not keys:
                sys.exit(f"Aucune clé pour {m} (model_keys.json ou OPENROUTER_API_KEYS).")
            specs.append({"name": m, "kind": "openrouter", "key": keys[0], "keys": keys, "model": m})
        return specs
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
    ap = argparse.ArgumentParser(description="Banc d'essai des modèles LLM par rôle")
    ap.add_argument("pdf", help="PDF de bulletin (ses annonces servent d'échantillon)")
    ap.add_argument("--role", default="extracteur", choices=["extracteur", "juge", "scoreur", "all"],
                    help="rôle testé ('all' = les 3 + rapport consolidé)")
    ap.add_argument("--models", help="IDs séparés par des virgules (sinon preset du rôle)")
    ap.add_argument("--provider", default="openrouter", choices=["openrouter", "gemini", "openai_compat"])
    ap.add_argument("--n", type=int, default=6, help="nombre d'annonces testées")
    ap.add_argument("--gold", help="JSON de vérité terrain (rôle extracteur)")
    ap.add_argument("--out", default="bench_results.json")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="affiche la progression en direct (chaque appel, succès/échec, durée)")
    ap.add_argument("--timeout", type=float, default=90,
                    help="secondes max par appel (coupe un modèle qui rame ; défaut 90)")
    ap.add_argument("--ab-models", dest="ab_models",
                    help="rôle juge : modèle(s) générant les extractions A/B "
                         "(1 ou 2 slugs). Défaut = 2 premiers extracteurs du roster. "
                         "Mets un modèle QUI RÉPOND, ex. liquid/lfm-2.5-2.6b:free")
    args = ap.parse_args()
    global VERBOSE
    VERBOSE = VERBOSE or args.verbose
    import layer3_extract_llm as _l3
    _l3.LLM_TIMEOUT = (10, args.timeout)   # coupe les modèles trop lents

    from pdf_avis_extract import segment_avis_blocks
    from section_split import split_sections
    zones = split_sections(args.pdf)
    blocks = segment_avis_blocks(args.pdf, page_range=zones["AVIS"])
    texts = [b["flat_text"] for b in blocks if b.get("flat_text")][:args.n]
    if not texts:
        sys.exit("Aucune annonce extraite du PDF.")

    def _run_role(role: str) -> dict:
        specs = _specs_from_models(
            [m.strip() for m in args.models.split(",") if m.strip()] if (args.models and role == args.role)
            else ROLE_PRESETS[role], args.provider)
        log.info("Banc [%s] : %d modèle(s) × %d annonce(s)", role, len(specs), len(texts))
        if role == "extracteur":
            gold = json.loads(Path(args.gold).read_text(encoding="utf-8")) if args.gold else None
            return evaluate_models(texts, specs, gold=gold)
        if role == "juge":
            ab_models = ([m.strip() for m in args.ab_models.split(",") if m.strip()]
                         if args.ab_models else ROLE_PRESETS["extracteur"][:2])
            if len(ab_models) == 1:          # un seul modèle -> sert pour A ET B
                ab_models = ab_models * 2
            ab_specs = _specs_from_models(ab_models[:2], "openrouter")
            schema = build_json_schema(load_field_schema("AVIS"))
            log.info("Génération A/B via %s + %s…", ab_specs[0]["model"], ab_specs[1]["model"])
            ab = [(real_caller(ab_specs[0], t, schema)[0] or {},
                   real_caller(ab_specs[1], t, schema)[0] or {}) for t in texts]
            return evaluate_juges(texts, specs, ab)
        return evaluate_scoreurs(texts, specs)

    roles = ["extracteur", "juge", "scoreur"] if args.role == "all" else [args.role]
    results_by_role = {}
    for role in roles:
        res = _run_role(role)
        results_by_role[role] = res
        print(f"\n### {role}\n" + format_table(res, role))
        Path(f"bench_{role}.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = {"pdf": Path(args.pdf).name, "n": len(texts),
            "date": time.strftime("%Y-%m-%d %H:%M")}
    report = build_report(results_by_role, meta, tokens=token_tracker.report())
    Path("bench_report.md").write_text(report, encoding="utf-8")
    log.info("Rapport -> bench_report.md | détail -> bench_<role>.json")


if __name__ == "__main__":
    main()
