"""
Analyseur de DIVERGENCE — comprendre POURQUOI deux extractions LLM diffèrent.

Le juge ne doit pas suivre bêtement les modèles : quand A et B divergent sur un
champ, il faut typer la CAUSE (déterministe), pas seulement constater l'écart.
Causes reconnues :

  format              : même valeur à la casse/aux accents/espaces près (faux écart)
  champ_vide          : un modèle a extrait, l'autre a laissé vide
  echange_de_champs   : A a mis dans X ce que B a mis dans Y (et inversement)
                        — typiquement date_limite_depot ↔ date_ouverture_plis
  htva_ttc            : deux montants, l'un HT l'autre TTC (les deux justes)
  valeur_proche       : même date à un format près / montants quasi égaux
  contradiction       : vraies valeurs différentes, non dérivables → revue humaine

Ces causes enrichissent `_consensus`, alimentent le juge (contrôle
`consensus_coherent`) et la boucle de rétroaction (raisons typées). Pur /
testable hors-ligne.
"""

import re
from typing import Optional

from judge import fold, parse_amounts, has_date

# Champs « durables » : une contradiction y déclenche la revue humaine.
DURABLE_FIELDS = {"autorite_emettrice", "objet", "date_limite_depot",
                  "montant_previsionnel_global", "numero_reference"}


def _flat(v) -> str:
    """Valeur aplatie comparable (gère dict/list), accents/casse normalisés."""
    if isinstance(v, dict):
        return " ".join(f"{k}={fold(x)}" for k, x in sorted(v.items()) if x and str(x).strip())
    if isinstance(v, (list, tuple)):
        return " ".join(fold(x) for x in v)
    return fold(v)


def _amounts(v) -> set:
    s = _flat(v)
    out = set(parse_amounts(s))
    for m in re.finditer(r"\(\s*(\d{3,})\s*\)", s):
        out.add(int(m.group(1)))
    return out


def _is_empty(v) -> bool:
    return not _flat(v)


def _find_swap(f: str, a: dict, b: dict, field_defs) -> Optional[str]:
    """Champ g tel que A[f]==B[g] et B[f]==A[g] (échange croisé). None sinon."""
    af, bf = _flat(a.get(f)), _flat(b.get(f))
    if not af or not bf:
        return None
    for g in field_defs:
        if g == f:
            continue
        if _flat(b.get(g)) == af and _flat(a.get(g)) == bf:
            return g
    return None


def classify_field(f: str, a: dict, b: dict, field_defs, flat_text: str = "") -> Optional[dict]:
    """Diagnostic d'un champ en désaccord. None si A et B concordent réellement."""
    va, vb = a.get(f), b.get(f)
    fa, fb = _flat(va), _flat(vb)
    if fa == fb:
        return None  # pas de divergence réelle

    def diag(cause, gravite, explication):
        return {"champ": f, "a": fa, "b": fb, "cause": cause,
                "gravite": gravite, "explication": explication}

    # 1) un modèle vide
    if not fa or not fb:
        vide, plein = ("A", "B") if not fa else ("B", "A")
        return diag("champ_vide", 1, f"modèle {vide} n'a rien extrait, modèle {plein} oui")

    # 2) échange de champs (ex. date_limite ↔ date_ouverture)
    g = _find_swap(f, a, b, field_defs)
    if g:
        return diag("echange_de_champs", 2, f"valeurs interverties avec « {g} »")

    # 3) montant HTVA vs TTC
    if "montant" in f:
        ca, cb = _amounts(va), _amounts(vb)
        if ca and cb and ca != cb:
            tax = re.search(r"htva|hors\s+taxe|ttc|toutes\s+taxes", fold(flat_text))
            if tax:
                return diag("htva_ttc", 1, "montants HT/TTC distincts, probablement tous deux corrects")
            return diag("contradiction", 3, "montants différents sans mention HT/TTC")

    # 4) dates : même jour à un format près
    if "date" in f and has_date(fa) and has_date(fb):
        da = re.findall(r"\d{1,4}", fa)
        db = re.findall(r"\d{1,4}", fb)
        if set(da) == set(db):
            return diag("valeur_proche", 1, "même date à un format d'écriture près")
        return diag("contradiction", 3, "dates différentes")

    # 5) sinon : contradiction réelle
    return diag("contradiction", 3, "valeurs différentes non dérivables l'une de l'autre")


def analyze_divergence(a: dict, b: dict, field_defs, flat_text: str = "") -> dict:
    """Analyse complète A vs B. Retourne la liste typée des divergences, le nombre
    de contradictions réelles, le drapeau de revue humaine et des raisons typées
    (pour la boucle de rétroaction)."""
    divergences = []
    for f in field_defs:
        d = classify_field(f, a, b, field_defs, flat_text)
        if d:
            divergences.append(d)

    contradictions = [d for d in divergences if d["cause"] == "contradiction"]
    # Revue humaine : une contradiction réelle sur un champ durable.
    alerte = any(d["champ"] in DURABLE_FIELDS for d in contradictions)
    raisons = [f"{d['champ']} : {d['cause']} ({d['explication']})" for d in divergences
               if d["gravite"] >= 2]
    return {
        "divergences": divergences,
        "n_contradictions": len(contradictions),
        "alerte_humaine": alerte,
        "raisons": raisons,
    }
