"""
LE JUGE (orchestrateur) — combine le SCOREUR (code pur) et le JUGE-LLM (3ᵉ modèle)
pour trancher chaque annonce en 🟢 VERT / 🟡 JAUNE / 🔴 ROUGE.

Architecture (option X) : le juge a DEUX moitiés qui travaillent en parallèle et
se surveillent.
  - Scoreur (scoreur.py)   : code pur, déterministe, score 0-100 + proba/critère.
  - Juge-LLM (juge_llm.py) : 3ᵉ modèle, lit texte + A + B, tranche, corrige, explique.

Décision combinée :
  - 🔴 ROUGE si les deux juges S'OPPOSENT (l'un dit bon, l'autre mauvais) — on
       enregistre POURQUOI ;
  - 🔴 ROUGE direct si l'un des deux juge l'extraction « mauvaise » ;
  - 🔴 ROUGE (avec 1 RÉESSAI auto) si un champ CRITIQUE est absent ou en
       contradiction non arbitrée ;
  - 🟡 JAUNE si un champ non critique diverge, ou doute léger → petite file humaine ;
  - 🟢 VERT sinon → base de données.

Seuil JAUNE→ROUGE DYNAMIQUE (pas de nombre codé en dur) : il dépend À LA FOIS
du score global du scoreur (beaucoup de petits défauts font chuter le score →
verdict « mauvais » → rouge) ET du poids des champs (un seul champ CRITIQUE en
défaut bascule seul en rouge).

Mode SÉVÈRE automatique quand un seul extracteur est disponible (ou 0 clé).

Ce module N'ÉCRASE PAS judge.py (primitives + RÉSULTATS), qu'il réutilise.
"""

import logging
from typing import Optional, Callable

import judge                     # primitives déterministes + judge_resultats (RÉSULTATS)
import scoreur
import juge_llm
import extraction as _extraction
import revue_humaine
from divergence_analyzer import analyze_divergence
from layer3_extract_llm import load_field_schema, build_providers

log = logging.getLogger("scrapperbf.juge")


# --------------------------------------------------------------- utilitaires
def _merge(a: dict, b: Optional[dict], field_defs: dict) -> dict:
    """Fusion best-effort A/B (pour le scoreur quand il n'y a pas d'arbitre LLM) :
    valeur de A si non vide, sinon celle de B."""
    out = {}
    b = b or {}
    for f in field_defs:
        va = a.get(f) if a else None
        out[f] = va if not judge.is_empty(va) else b.get(f)
    return out


def _present(champs: dict, field: str) -> bool:
    if field == "montant_previsionnel_global":
        return bool(judge.montant_amounts(champs))
    return not judge.is_empty(champs.get(field))


# --------------------------------------------------------------- décision
def juger_avis(text: str, a: dict, b: Optional[dict], section: str = "AVIS",
               providers: Optional[list] = None, severe: bool = False,
               juge_llm_fn: Optional[Callable] = None,
               autoriser_reessai: bool = True) -> dict:
    """Décision du juge pour UNE annonce, à partir des extractions A et B.

    `juge_llm_fn(text, a, b)` est injectable pour les tests hors-ligne ; sinon on
    appelle juge_llm.review si des `providers` sont fournis. Retourne la décision
    complète (couleur, action, champs finaux, raisons, conflits…)."""
    field_defs = load_field_schema(section)
    champs_ab = _merge(a, b, field_defs)

    # --- Moitié LLM (peut être absente) ---
    if juge_llm_fn is not None:
        llm = juge_llm_fn(text, a, b)
    elif providers:
        llm = juge_llm.review(text, a, b, section=section, providers=providers)
    else:
        llm = None

    if llm and isinstance(llm.get("champs"), dict) and llm["champs"]:
        champs_finaux = dict(llm["champs"])
        verdict_llm = llm.get("verdict", "doute")
    else:
        champs_finaux = champs_ab
        verdict_llm = None
        llm = llm or None

    # --- Moitié CODE PUR (toujours) ---
    sc = scoreur.scorer_avis(champs_finaux, text, severe=severe, section=section)
    verdict_sc = sc["verdict"]

    # --- Divergences A/B typées (déterministe) ---
    divs = analyze_divergence(a, b, field_defs, text)["divergences"] if b else []
    crit = set(scoreur.champs_critiques(section))

    champs_en_conflit = []
    for d in divs:
        champs_en_conflit.append({
            "champ": d["champ"], "cause": d["cause"], "gravite": d["gravite"],
            "critique": d["champ"] in crit, "a": d["a"], "b": d["b"],
        })
    # Corrections du juge-LLM = points que le modèle a dû rectifier (informatif).
    for c in (llm.get("corrections") if llm else []) or []:
        ch = c.get("champ")
        if ch and ch not in {x["champ"] for x in champs_en_conflit}:
            champs_en_conflit.append({"champ": ch, "cause": "correction_llm",
                                      "gravite": 2, "critique": ch in crit,
                                      "a": c.get("ancienne"), "b": c.get("nouvelle")})

    # --- Problèmes sur champs CRITIQUES (forcent le rouge) ---
    crit_problem = []
    contradictions = {d["champ"] for d in divs if d["cause"] == "contradiction"}
    for f in crit:
        if not _present(champs_finaux, f):
            crit_problem.append({"champ": f, "cause": "absent"})
        elif f in contradictions and llm is None:
            crit_problem.append({"champ": f, "cause": "divergence_non_arbitree"})

    conflit_non_critique = any(c["gravite"] >= 2 and not c["critique"]
                               for c in champs_en_conflit)

    # --- Combinaison des verdicts ---
    oppose = verdict_llm is not None and {verdict_sc, verdict_llm} == {"bon", "mauvais"}
    any_mauvais = verdict_sc == "mauvais" or verdict_llm == "mauvais"

    pourquoi = None
    regle_suggeree = None
    if oppose:
        couleur, action = "rouge", "humain"
        pourquoi = (f"désaccord des juges : juge-LLM dit « {verdict_llm} », "
                    f"scoreur dit « {verdict_sc} » (score {sc['score']})")
        raison = "Les deux moitiés du juge se contredisent -> alerte humaine."
    elif any_mauvais:
        couleur, action = "rouge", "humain"
        qui = "les deux juges" if verdict_sc == "mauvais" and verdict_llm == "mauvais" else \
              ("le scoreur" if verdict_sc == "mauvais" else "le juge-LLM")
        raison = f"Extraction jugée mauvaise par {qui} -> alerte humaine (pas de réessai)."
    elif crit_problem:
        couleur = "rouge"
        champs_pb = ", ".join(f"{p['champ']} ({p['cause']})" for p in crit_problem)
        if autoriser_reessai:
            action = "reessayer"
            raison = f"Champ(s) critique(s) en défaut : {champs_pb} -> 1 réessai."
        else:
            action = "humain"
            raison = f"Champ(s) critique(s) toujours en défaut après réessai : {champs_pb}."
        regle_suggeree = {
            "champs": [p["champ"] for p in crit_problem],
            "hint": ("Relis attentivement le texte pour renseigner/justifier ces "
                     "champs essentiels : " + champs_pb + ". "
                     "Ne laisse vide que si l'information est RÉELLEMENT absente."),
        }
    elif conflit_non_critique or verdict_sc == "doute" or verdict_llm == "doute":
        couleur, action = "jaune", "file_humaine"
        jc = [c["champ"] for c in champs_en_conflit if not c["critique"]]
        raison = ("Doute sur champ(s) non critique(s) : " + ", ".join(jc)
                  if jc else "Doute léger sur l'extraction.") + " -> petite file humaine."
    else:
        couleur, action = "vert", "base"
        raison = "Scoreur et juge-LLM concordent, aucun champ critique en défaut -> base."

    revue = couleur in ("jaune", "rouge")
    return {
        "couleur": couleur,
        "action": action,
        "revue_humaine": revue,
        "raison": raison,
        "pourquoi_divergence_juges": pourquoi,
        "regle_suggeree": regle_suggeree,
        "champs_finaux": {k: v for k, v in champs_finaux.items() if not str(k).startswith("_")},
        "score": sc["score"],
        "probabilites": sc["probabilites"],
        "verdict_scoreur": verdict_sc,
        "verdict_llm": verdict_llm,
        "champs_critiques_manquants": sc["champs_critiques_manquants"],
        "champs_en_conflit": champs_en_conflit,
        "divergences": divs,
        "analyse_llm": (llm or {}).get("analyse", ""),
        "severe": bool(severe),
        "extraction_a": a,
        "extraction_b": b,
    }


# --------------------------------------------------------------- orchestration
def traiter_avis(text: str, section: str = "AVIS", numero: str = "?", index: int = 0,
                 providers: Optional[list] = None, severe: Optional[bool] = None,
                 extracteur: Optional[Callable] = None,
                 juge_llm_fn: Optional[Callable] = None,
                 ecrire_dossier: bool = True) -> Optional[dict]:
    """Chaîne complète pour une annonce : extraction A/B -> juge -> (1 réessai si
    champ critique) -> dossier humain si 🟡/🔴. Retourne la décision finale, ou
    None si aucun provider (l'appelant retombe sur la chaîne déterministe)."""
    extracteur = extracteur or _extraction.extract_fields
    provs = providers if providers is not None else build_providers()
    if severe is None:
        severe = len(provs) <= 1

    ex = extracteur(text, section, providers=provs)
    if ex is None:
        return None
    severe = severe or ex.get("degrade", False)

    dec = juger_avis(text, ex["a"], ex["b"], section, providers=provs,
                     severe=severe, juge_llm_fn=juge_llm_fn, autoriser_reessai=True)
    dec["regles_appliquees"] = ex.get("regles_appliquees", [])

    # 3ᵉ issue : 1 seul réessai auto, avec la consigne ciblée du juge.
    if dec["action"] == "reessayer":
        hint = (dec.get("regle_suggeree") or {}).get("hint", "")
        log.info("[%s] réessai extraction (champ critique) : %s", numero, hint[:80])
        ex2 = extracteur(text, section, providers=provs, hint=hint)
        if ex2 is not None:
            dec = juger_avis(text, ex2["a"], ex2["b"], section, providers=provs,
                             severe=severe or ex2.get("degrade", False),
                             juge_llm_fn=juge_llm_fn, autoriser_reessai=False)
            dec["regles_appliquees"] = ex2.get("regles_appliquees", [])
        dec["_reessaye"] = True

    if ecrire_dossier and dec["couleur"] in ("jaune", "rouge"):
        try:
            dossier = revue_humaine.creer_dossier(dec, text, numero, index, section)
            dec["_dossier"] = dossier.get("id")
        except Exception as e:  # ne jamais casser le pipeline pour un dossier
            log.warning("[%s] écriture dossier revue impossible : %s", numero, e)

    return dec


# --------------------------------------------------------------- compat pipeline
def to_judge_compat(dec: dict) -> dict:
    """Convertit une décision v2 vers l'ancien format `_judge` attendu par le
    pipeline (meta : confiance moyenne, nb revue humaine)."""
    verdict = {"vert": "valide", "jaune": "a_verifier", "rouge": "rejete"}[dec["couleur"]]
    return {
        "verdict": verdict,
        "confiance": round(dec["score"] / 100, 3),
        "revue_humaine": dec["revue_humaine"],
        "couleur": dec["couleur"],
        "anomalies": [{"nom": c["champ"], "statut": "warn" if not c["critique"] else "fail",
                       "detail": c.get("cause", "")} for c in dec.get("champs_en_conflit", [])],
    }
