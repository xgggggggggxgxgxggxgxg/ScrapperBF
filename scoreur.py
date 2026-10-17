"""
SCOREUR — la moitié CODE PUR du juge (0 token, déterministe).

Il tourne EN PARALLÈLE du juge-LLM et donne un second avis, indépendant et
reproductible. Il réutilise les contrôles déterministes de judge.py (montant
plausible, ratio caution, lots cohérents, texte lisible, complétude…) et en tire :

  - un SCORE [0..100] ;
  - une PROBABILITÉ par critère (0..1) ;
  - un VERDICT grossier : "bon" | "doute" | "mauvais" ;
  - la liste des CHAMPS CRITIQUES manquants (poids fort pour la décision du juge).

Mode SÉVÈRE (`severe=True`) : appliqué quand un seul extracteur est disponible
(ou 0 clé). Les `warn` pèsent plus lourd et les seuils de verdict montent -> plus
d'annonces basculent en doute/mauvais, donc en revue humaine.

Aucune dépendance réseau. Testable hors-ligne.
"""

import re
import logging
from functools import lru_cache
from typing import Optional

import judge
from layer3_extract_llm import load_field_schema

log = logging.getLogger("scrapperbf.scoreur")

# Mots de grandeur : un montant peut être écrit en toutes lettres (« quatre-vingts
# millions ») sans groupe de chiffres parsable. On considère alors le champ présent.
_MONTANT_MOTS = re.compile(r"\b(mille|millions?|milliards?)\b", re.I)

# Pondération statut -> probabilité. En mode sévère, un "warn" (doute) compte
# presque comme un échec.
_PROBA = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
_PROBA_SEVERE = {"pass": 1.0, "warn": 0.25, "fail": 0.0}

# Seuils de verdict (score). Montent en mode sévère.
_SEUILS = {False: (85, 60), True: (92, 75)}  # (bon si >=, mauvais si <)


@lru_cache(maxsize=8)
def champs_critiques(section: str = "AVIS") -> tuple:
    """Champs marqués `critique: true` dans schema_config.yaml (cache : le schéma
    ne change pas en cours d'exécution)."""
    return tuple(f for f, spec in load_field_schema(section).items()
                 if isinstance(spec, dict) and spec.get("critique"))


def present(champs: dict, field: str) -> bool:
    """Un champ est 'présent' s'il porte une vraie valeur. Le montant exige un
    montant réellement parsé (un champ rempli de n° de téléphone ne compte pas),
    OU un montant écrit en toutes lettres (« … millions »)."""
    if field == "montant_previsionnel_global":
        if judge.montant_amounts(champs):
            return True
        v = champs.get("montant_previsionnel_global")
        s = " ".join(str(x) for x in v.values() if x) if isinstance(v, dict) else str(v or "")
        return bool(_MONTANT_MOTS.search(s))
    return not judge.is_empty(champs.get(field))


# Alias interne rétrocompatible.
_present = present


def scorer_avis(champs: dict, flat_text: str = "", severe: bool = False,
                section: str = "AVIS") -> dict:
    """Score déterministe d'un avis extrait. `champs` = valeurs finales (ou A/B
    fusionné). Réutilise les contrôles de judge.judge_avis."""
    res = judge.judge_avis(champs, flat_text)
    checks = res.get("checks", [])

    table = _PROBA_SEVERE if severe else _PROBA
    num = den = 0.0
    probabilites = {}
    for c in checks:
        if c["statut"] == "na":
            continue
        p = table.get(c["statut"], 0.0)
        probabilites[c["nom"]] = round(p, 3)
        num += c["poids"] * p
        den += c["poids"]
    score = round(100 * num / den) if den else 0

    seuil_bon, seuil_mauvais = _SEUILS[bool(severe)]
    if score >= seuil_bon:
        verdict = "bon"
    elif score < seuil_mauvais:
        verdict = "mauvais"
    else:
        verdict = "doute"

    crit = champs_critiques(section)
    manquants = [f for f in crit if not _present(champs, f)]
    # Un champ critique manquant ne peut JAMAIS donner un verdict "bon".
    if manquants and verdict == "bon":
        verdict = "doute"

    return {
        "score": score,
        "probabilites": probabilites,
        "verdict": verdict,
        "champs_critiques_manquants": manquants,
        "severe": bool(severe),
        "anomalies": res.get("anomalies", []),
    }
