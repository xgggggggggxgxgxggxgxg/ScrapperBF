"""
Scoreur comportemental (type Span-01) — SIGNAL SOUPLE, en PARALLÈLE du juge.

Le juge Python est déterministe et gratuit (0 token) : il reste la référence.
Ce module ajoute un 2ᵉ avis, « flou » : pour chaque comportement décrit en
langage simple, un modèle renvoie une PROBABILITÉ (0..1) qu'il soit vrai pour
l'annonce. Utile pour rattraper des annonces « qui ont l'air bâclées » que les
règles n'expriment pas.

- OPTIONNEL : activé seulement si ENABLE_SPAN_SCORING=1 ET une clé SPAN_API_KEY(S).
  Inerte sinon (aucun appel, aucun coût).
- NON bloquant : une erreur réseau n'interrompt jamais l'extraction.
- EN PARALLÈLE : score_many() lance les appels en threads, à côté du juge.
- Comptage des tokens via token_tracker (kind = "span").

Le moteur de scoring est INJECTABLE (tests hors-ligne avec une doublure).
"""

import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Callable

import config
import token_tracker

log = logging.getLogger("scrapperbf.span")

# Comportements par défaut (adaptés aux avis de marché). Les « critiques »
# déclenchent la revue humaine s'ils sont improbables.
DEFAULT_BEHAVIORS = [
    "l'annonce indique une date limite de dépôt des offres",
    "le montant prévisionnel est présent (HTVA et/ou TTC)",
    "l'autorité émettrice est clairement identifiée",
    "l'objet du marché est précis et compréhensible",
    "le texte de l'annonce semble complet (non tronqué)",
    "le texte est lisible (pas de caractères corrompus)",
]
CRITICAL_BEHAVIORS = {
    "l'annonce indique une date limite de dépôt des offres",
    "l'autorité émettrice est clairement identifiée",
    "le texte de l'annonce semble complet (non tronqué)",
}

SPAN_SYSTEM = (
    "Tu es un évaluateur de qualité. Pour CHAQUE comportement listé, donne la "
    "PROBABILITÉ entre 0.0 et 1.0 qu'il soit VRAI pour le texte d'annonce fourni. "
    "Réponds UNIQUEMENT par un objet JSON {\"<comportement>\": <probabilité>, ...} "
    "reprenant EXACTEMENT les comportements donnés, sans rien autour."
)


def enabled() -> bool:
    flag = os.environ.get("ENABLE_SPAN_SCORING", "0") not in ("0", "false", "False", "")
    return flag and config.span_spec() is not None


def _clamp(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _default_scorer(spec: dict) -> Callable:
    """Scoreur réel : appelle un endpoint compatible OpenAI (OpenRouter par
    défaut) et parse le JSON de probabilités. Compte les tokens."""
    from layer3_extract_llm import call_openai_compatible, ProviderError
    key = spec["keys"][0]

    def run(text: str, behaviors: list) -> dict:
        prompt = ("Comportements :\n" + "\n".join(f"- {b}" for b in behaviors)
                  + f"\n\nTexte de l'annonce :\n{text}")
        try:
            res = call_openai_compatible(prompt, {}, key, spec["base_url"], spec["model"],
                                         system_prompt=SPAN_SYSTEM, strict=False)
        except Exception as e:  # réseau / quota -> non bloquant
            token_tracker.record_error("span", spec["model"], key, str(e)[:80])
            log.warning("span : échec scoring (%s)", str(e)[:100])
            return {}
        usage = res.pop("_usage", None) if isinstance(res, dict) else None
        if usage:
            token_tracker.record("span", spec["model"], key, usage)
        return {b: _clamp(res.get(b)) for b in behaviors}

    return run


def score_behaviors(text: str, behaviors: Optional[list] = None,
                    scorer: Optional[Callable] = None, seuil: float = 0.5) -> dict:
    """Score une annonce. Retourne {scores, comportements_faibles, revue_humaine,
    seuil}. `scorer(text, behaviors)->{comportement: proba}` est injectable."""
    behaviors = behaviors or DEFAULT_BEHAVIORS
    if scorer is None:
        spec = config.span_spec()
        if spec is None:
            return {"scores": {}, "comportements_faibles": [], "revue_humaine": False,
                    "seuil": seuil, "indisponible": True}
        scorer = _default_scorer(spec)

    raw = scorer(text, behaviors) or {}
    # Normalisation défensive : toute valeur illisible/absente -> 0.0.
    scores = {b: _clamp(raw.get(b)) for b in behaviors}
    faibles = [b for b in behaviors if scores[b] < seuil]
    # Revue humaine si un comportement CRITIQUE est improbable.
    revue = any(b in CRITICAL_BEHAVIORS for b in faibles)
    return {"scores": scores, "comportements_faibles": faibles,
            "revue_humaine": revue, "seuil": seuil}


def score_many(records: list, text_key: str = "flat_text", behaviors: Optional[list] = None,
               scorer: Optional[Callable] = None, seuil: float = 0.5,
               max_workers: int = 4) -> None:
    """Score PLUSIEURS annonces en parallèle (threads) et attache `_span` à
    chacune. Remonte le drapeau de revue dans `_judge.revue_humaine`. No-op si
    aucun texte."""
    if scorer is None:
        spec = config.span_spec()
        if spec is None:
            return
        scorer = _default_scorer(spec)

    def work(rec):
        text = rec.get(text_key) or rec.get("texte_brut") or ""
        if not text:
            return
        span = score_behaviors(text, behaviors, scorer=scorer, seuil=seuil)
        rec["_span"] = span
        if span["revue_humaine"] and isinstance(rec.get("_judge"), dict):
            rec["_judge"]["revue_humaine"] = True

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(work, records))
