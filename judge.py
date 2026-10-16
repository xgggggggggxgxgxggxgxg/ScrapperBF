"""
Le JUGE — vérificateur de cohérence déterministe (inspiré du rôle « contrôleur
qualité » d'un moteur de décision type Jev AI).

Principe clé : les contrôles de cohérence entre le TEXTE source et les VALEURS
extraites sont déterministes et quasi gratuits — ils doivent tourner en local,
pas dans un LLM coûteux. Le juge NE génère rien : il pose des questions fermées
(pass / warn / fail / na), les pondère, et en tire :
  - une CONFIANCE [0..1],
  - un VERDICT ("valide" | "a_verifier" | "rejete"),
  - la liste des ANOMALIES,
  - un drapeau REVUE HUMAINE (confiance < seuil).

Il s'applique APRÈS extraction (regex 1re passe ou API) :
    regex → décision → (API) → JUGE → base / file de revue humaine

Exemples de contrôles (avis) :
  - ratio caution / budget plausible (≈ 1–2 %) ;
  - nombre de lots annoncé dans le texte == nombre de lots extrait ;
  - date limite présente et cohérente ;
  - montant dans une fourchette plausible (détecte un facteur 1000 d'erreur) ;
  - texte lisible (détection de corruption de police PDF) ;
  - avis complet (en-tête, objet, délais, signature de clôture) → pas tronqué.

Tout est pur / testable hors-ligne.
"""

import re
import unicodedata
from typing import Optional

# ------------------------------------------------------------------ primitives
_VOWELS = set("aeiouyàâäéèêëîïôöûüAEIOUYÀÂÄÉÈÊËÎÏÔÖÛÜ")
_AMOUNT_RE = re.compile(r"(\d{1,3}(?:[ . ]\d{3})+|\d{5,})")
_NUMWORDS = {
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
}


def fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    return False


def parse_amounts(text: str) -> list:
    """Montants entiers repérés (groupes « 80 000 000 » ou longues suites). Les
    années à 4 chiffres (2026) sont exclues faute de groupement."""
    out = []
    for m in _AMOUNT_RE.finditer(text or ""):
        try:
            out.append(int(re.sub(r"[ . ]", "", m.group(1))))
        except ValueError:
            pass
    return out


def amount_near(text: str, keyword_re: str, window: int = 100) -> Optional[int]:
    """Montant le PLUS PROCHE du mot-clé (et non le plus gros) dans une fenêtre
    courte — évite de rattacher un budget global voisin à « caution »."""
    m = re.search(keyword_re, text or "", re.I)
    if not m:
        return None
    seg = text[m.start(): m.start() + window]
    a = parse_amounts(seg)
    return a[0] if a else None


def garbling_score(text: str) -> float:
    """Proportion de « mots » longs sans voyelle = indice de texte corrompu
    (police PDF cassée). ~0 pour un texte propre, élevé si charabia."""
    toks = re.findall(r"[A-Za-zÀ-ÿ]{2,}", text or "")
    if not toks:
        return 1.0 if (text or "").strip() else 0.0
    bad = sum(1 for t in toks if len(t) >= 4 and not any(c in _VOWELS for c in t))
    return round(bad / len(toks), 3)


def declared_lot_count(text: str) -> Optional[int]:
    """Nombre de lots ANNONCÉ dans le texte (« lot unique », « en trois (03)
    lots »…). None si indéterminé."""
    f = fold(text)
    if re.search(r"lot unique|un\s*\(?0?1\)?\s*lot|en un lot|lot\s+unique", f):
        return 1
    m = re.search(r"en\s+([a-zéèê]+|\d{1,2})\s*\(?\s*(\d{1,2})?\s*\)?\s*lots", f)
    if m:
        if m.group(2):
            return int(m.group(2))
        w = m.group(1)
        if w.isdigit():
            return int(w)
        return _NUMWORDS.get(w)
    # repli : marqueurs « Lot N : » explicites
    nums = set(re.findall(r"lot\s*n?[°o]?\s*(\d{1,2})\s*:", f))
    return max(int(n) for n in nums) if nums else None


def has_date(s: str) -> bool:
    return bool(re.search(r"\d{1,2}[/\s.\-]\d{1,2}[/\s.\-]\d{2,4}"
                          r"|\d{1,2}\s+(janvier|février|fevrier|mars|avril|mai|juin|juillet|"
                          r"août|aout|septembre|octobre|novembre|décembre|decembre)", fold(s)))


# ------------------------------------------------------------------ moteur
def _confidence(checks: list) -> float:
    num = den = 0.0
    for c in checks:
        if c["statut"] == "na":
            continue
        w = c["poids"]
        s = {"pass": 1.0, "warn": 0.5, "fail": 0.0}.get(c["statut"], 0.0)
        num += w * s
        den += w
    return round(num / den, 3) if den else 0.0


def _verdict(conf: float, seuil_valide: float = 0.9, seuil_rejet: float = 0.6) -> str:
    if conf >= seuil_valide:
        return "valide"
    if conf >= seuil_rejet:
        return "a_verifier"
    return "rejete"


def _mk(nom, statut, poids, detail=""):
    return {"nom": nom, "statut": statut, "poids": poids, "detail": detail}


def montant_amounts(avis: dict) -> list:
    """Montants du champ montant : groupes de milliers « 80 000 000 » (via
    parse_amounts) ET nombres entre parenthèses « (500) ». Exclut les suites
    façon téléphone « 72 19 13 88 » (groupes de 2) et les années/délais isolés,
    pour ne pas prendre une ligne de contact pour un budget."""
    v = avis.get("montant_previsionnel_global")
    s = " ".join(str(x) for x in v.values() if x) if isinstance(v, dict) else str(v or "")
    out = set(parse_amounts(s))
    for m in re.finditer(r"\(\s*(\d{3,})\s*\)", s):  # (500), (50000000) sans séparateur
        try:
            out.add(int(m.group(1)))
        except ValueError:
            pass
    return sorted(out)


def judge_avis(avis: dict, flat_text: str = "",
               seuil_valide: float = 0.9) -> dict:
    """Juge un avis extrait. Retourne verdict / confiance / checks / anomalies /
    revue_humaine. `flat_text` = texte source (pour les contrôles croisés)."""
    text = flat_text or avis.get("flat_text", "") or ""
    f = fold(text)
    checks = []
    amts = montant_amounts(avis)           # montants réels du champ (calcul unique)
    declared = declared_lot_count(text)    # nb de lots annoncé dans le texte

    # 1. Complétude (champs clés présents). Pour le montant, « présent » exige un
    #    vrai montant parsé (un champ rempli de n° de téléphone ne compte pas).
    for field, poids, dur in [("autorite_emettrice", 1.0, True), ("objet", 1.0, True),
                              ("date_limite_depot", 0.9, True),
                              ("montant_previsionnel_global", 0.6, False),
                              ("numero_reference", 0.5, False),
                              ("type_avis", 0.4, False)]:
        if field == "montant_previsionnel_global":
            present = bool(amts)
        else:
            present = not is_empty(avis.get(field))
        checks.append(_mk(f"present:{field}", "pass" if present else ("fail" if dur else "warn"),
                          poids, "présent" if present else "manquant"))

    # 2. Texte lisible (corruption de police PDF)
    g = garbling_score(text)
    checks.append(_mk("texte_lisible", "pass" if g < 0.18 else ("warn" if g < 0.35 else "fail"),
                      0.8, f"indice corruption {g}"))

    # 3. Montant plausible (détecte un facteur 1000)
    if amts:
        mx = max(amts)
        ok = 50_000 <= mx <= 500_000_000_000
        checks.append(_mk("montant_plausible", "pass" if ok else "fail", 0.7,
                          f"max={mx} FCFA"))
    else:
        checks.append(_mk("montant_plausible", "na", 0.7, "montant absent"))

    # 4. Ratio caution / budget (≈ 1–2 % ; marge 0,2 %–5 %). On s'abstient sur
    #    les avis multi-lots (caution par lot vs budget global = comparaison faussée).
    caution = amount_near(text, r"garantie\s+de\s+soumission|garantie\s+financi[èe]re|caution")
    if caution and amts and not (declared and declared > 1):
        ratio = caution / max(amts)
        statut = "pass" if 0.002 <= ratio <= 0.05 else "warn"
        checks.append(_mk("ratio_caution", statut, 0.5, f"{ratio*100:.2f}% du budget"))
    else:
        checks.append(_mk("ratio_caution", "na", 0.5, "n/a (multi-lots ou caution/budget absent)"))

    # 5. Cohérence des lots (annoncé vs extrait)
    lots = avis.get("lots")
    n_extr = len(lots) if isinstance(lots, list) and lots else (
        1 if avis.get("est_en_lots") in (False, None) and declared == 1 else None)
    if declared is not None and n_extr is not None:
        checks.append(_mk("lots_coherents", "pass" if declared == n_extr else "warn", 0.5,
                          f"annoncés={declared}, extraits={n_extr}"))
    elif declared is not None and declared > 1 and not lots:
        checks.append(_mk("lots_coherents", "warn", 0.5, f"{declared} lots annoncés, aucun extrait"))
    else:
        checks.append(_mk("lots_coherents", "na", 0.5, ""))

    # 6. Date limite : format valide
    dl = avis.get("date_limite_depot")
    checks.append(_mk("date_limite_format", "pass" if dl and has_date(dl) else ("warn" if dl else "fail"),
                      0.5, "date lisible" if dl and has_date(dl) else "date absente/illisible"))

    # 7. Signature de clôture (avis non tronqué)
    sig = bool(re.search(r"le\s+(pr[ée]sident|directeur|secr[ée]taire|maire)|"
                         r"\bprm\b|personne\s+responsable|attach[ée]\s+administratif", f))
    checks.append(_mk("cloture_signature", "pass" if sig else "warn", 0.4,
                      "signature présente" if sig else "pas de signature détectée (tronqué ?)"))

    # 8. Référence bien formée
    ref = avis.get("numero_reference")
    checks.append(_mk("ref_format", "pass" if ref and re.search(r"\d{4}", str(ref)) else "warn",
                      0.3, ""))

    # 9. Cohérence du consensus : POURQUOI les deux modèles divergent. Une
    #    contradiction réelle (valeurs incompatibles, non HT/TTC ni reformatage)
    #    pèse ; un simple écart de format ou un HT/TTC ne pénalise pas.
    cons = avis.get("_consensus") or {}
    if cons:
        n_contra = cons.get("n_contradictions", 0)
        raisons = cons.get("raisons_divergence") or []
        if n_contra:
            checks.append(_mk("consensus_coherent", "fail", 0.7,
                              f"{n_contra} contradiction(s) : " + " ; ".join(raisons[:3])))
        elif raisons:  # écarts structurants non contradictoires (échange, champ vide)
            checks.append(_mk("consensus_coherent", "warn", 0.7, " ; ".join(raisons[:3])))
        else:
            checks.append(_mk("consensus_coherent", "pass", 0.7, "modèles concordants"))
    else:
        checks.append(_mk("consensus_coherent", "na", 0.7, "pas de consensus (mode sans API)"))

    conf = _confidence(checks)
    verdict = _verdict(conf, seuil_valide)
    anomalies = [{"nom": c["nom"], "statut": c["statut"], "detail": c["detail"]}
                 for c in checks if c["statut"] in ("fail", "warn")]
    # Une CONTRADICTION réelle du consensus force la revue humaine et plafonne le
    # verdict, même si la moyenne pondérée reste haute (« si divergent trop → alerte »).
    contradiction = (avis.get("_consensus") or {}).get("n_contradictions", 0)
    revue = conf < seuil_valide or bool(contradiction)
    if contradiction and verdict == "valide":
        verdict = "a_verifier"
    return {
        "verdict": verdict,
        "confiance": conf,
        "revue_humaine": revue,
        "checks": checks,
        "anomalies": anomalies,
    }


def judge_resultats(res: dict, flat_text: str = "", seuil_valide: float = 0.9) -> dict:
    """Juge une unité RÉSULTATS (structurée par l'API, ou déterministe).
    Contrôles : présence de l'émetteur, cohérence du nombre de lots, présence
    d'un attributaire (ou infructueux) par lot, montants plausibles."""
    text = flat_text or res.get("texte_brut", "") or ""
    checks = []
    checks.append(_mk("present:autorite", "pass" if not is_empty(res.get("autorite_contractante")) else "fail", 1.0))

    lots = res.get("lots") or []
    declared = declared_lot_count(text)
    n = res.get("nombre_lots") if res.get("nombre_lots") is not None else len(lots)
    if declared is not None and n is not None:
        checks.append(_mk("lots_coherents", "pass" if declared == n else "warn", 0.7,
                          f"annoncés={declared}, structurés={n}"))
    else:
        checks.append(_mk("lots_coherents", "na", 0.7))

    structured = [l for l in lots if isinstance(l, dict) and l.get("soumissionnaires")]
    if structured:
        with_att = sum(1 for l in structured if l.get("attributaire_provisoire") or l.get("infructueux"))
        checks.append(_mk("attributaires", "pass" if with_att == len(structured) else "warn", 0.6,
                          f"{with_att}/{len(structured)} lots avec décision"))
        # Montants plausibles
        amts = parse_amounts(" ".join(
            str(s.get(k, {}).get(b, "")) for l in structured for s in (l.get("soumissionnaires") or [])
            for k in ("montant_lu_ttc", "montant_corrige_ttc") for b in ("min", "max")
            if isinstance(s.get(k), dict)))
        if amts:
            mx = max(amts)
            checks.append(_mk("montants_plausibles", "pass" if 10_000 <= mx <= 10**13 else "warn", 0.5, f"max={mx}"))
        else:
            checks.append(_mk("montants_plausibles", "na", 0.5))
    else:
        # mode déterministe : on juge seulement la segmentation
        checks.append(_mk("attributaires", "na", 0.6, "non structuré (déterministe)"))
        checks.append(_mk("montants_plausibles", "na", 0.5))

    conf = _confidence(checks)
    return {
        "verdict": _verdict(conf, seuil_valide),
        "confiance": conf,
        "revue_humaine": conf < seuil_valide,
        "checks": checks,
        "anomalies": [{"nom": c["nom"], "statut": c["statut"], "detail": c["detail"]}
                      for c in checks if c["statut"] in ("fail", "warn")],
    }
