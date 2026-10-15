"""
Modèle de décision — la couche entre la 1re passe (regex) et l'appel LLM.

Idée (demande utilisateur) : après l'extraction regex, un modèle regarde LE TEXTE
de l'annonce ET les valeurs extraites. S'il voit un problème — un champ vide alors
que l'info est clairement dans le texte, ou une valeur « sale » / à reclasser
(ex. le montant prévisionnel contient DEUX valeurs justes HTVA + TTC collées, ou
une queue parasite « … TTC Cet ») — il décide de RENVOYER À L'API pour une
meilleure extraction, en pointant précisément les champs à reprendre. L'API peut
alors adapter/classer les variables (htva vs ttc, etc.).

But : ne payer l'appel LLM QUE quand il apporte quelque chose, et le cibler.
Purement déterministe et testable hors-ligne.
"""

import re
import unicodedata
from typing import Optional

# Types de problème détectés.
PB_VIDE_REQUIS = "vide_requis"            # champ requis vide
PB_VIDE_PRESENT = "vide_mais_present"     # vide alors qu'un indice textuel le trouve
PB_A_CLASSER = "a_classer"                # valeur brute à reclasser (ex. htva/ttc)
PB_QUEUE_PARASITE = "queue_parasite"      # la valeur déborde sur la phrase suivante
PB_TROP_LONG = "trop_long"                # valeur anormalement longue

REQUIRED = ("autorite_emettrice", "objet")

# Indices textuels : si le champ est vide mais que l'un de ces mots est dans le
# texte, c'est que l'info existe et que la 1re passe l'a ratée -> à reprendre.
CUE_WORDS = {
    "date_limite_depot": ["parvenir", "au plus tard", "avant le", "date limite",
                          "remise des offres", "deposees", "depot des offres"],
    "montant_previsionnel_global": ["montant previsionnel"],
    "financement": ["financement", "budget"],
    "numero_reference": ["n°", "n :", "no "],
    "delai_execution": ["delai d'execution", "delai d execution"],
    "date_ouverture_plis": ["ouverture des plis", "ouverture faite"],
}

# Mots qui, en fin de valeur, trahissent un débordement sur la phrase suivante.
_TAIL_WORDS_RE = re.compile(
    r"\b(Cet|Cette|La\s+participation|Le\s+pr[ée]sent|Le\s+Directeur|Le\s+Pr[ée]sident|"
    r"Identifiant|Ma[îi]tre|Les\s+candidats|Il\s+est)\b\s*\S*$", re.I)

MAX_LEN = {"autorite_emettrice": 110, "objet": 240, "numero_reference": 80,
           "type_avis": 70, "financement": 170, "delai_execution": 130,
           "date_limite_depot": 130}


def _fold(s) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def _is_empty(v) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v.strip() == ""
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    return False


def montant_a_classer(value) -> bool:
    """Le montant prévisionnel est « à classer » s'il est encore brut : un dict
    {brut: …}, ou une chaîne qui contient à la fois HTVA et TTC (deux valeurs)."""
    if isinstance(value, dict):
        if "brut" in value and not (value.get("htva") or value.get("ttc")):
            return True
        return False
    if isinstance(value, str):
        f = _fold(value)
        return ("htva" in f and "ttc" in f) or bool(re.search(r"\bcet\b$", f))
    return False


def analyser_champ(field: str, value, folded_text: str) -> Optional[dict]:
    """Retourne un problème {champ, type, detail} pour ce champ, ou None."""
    if _is_empty(value):
        cues = [c for c in CUE_WORDS.get(field, []) if c in folded_text]
        if cues:
            return {"champ": field, "type": PB_VIDE_PRESENT,
                    "detail": f"vide mais le texte contient « {cues[0]} »"}
        if field in REQUIRED:
            return {"champ": field, "type": PB_VIDE_REQUIS, "detail": "champ requis vide"}
        return None

    if field == "montant_previsionnel_global" and montant_a_classer(value):
        return {"champ": field, "type": PB_A_CLASSER,
                "detail": "montant brut à classer en htva / ttc"}

    if isinstance(value, str):
        if _TAIL_WORDS_RE.search(value.strip()):
            return {"champ": field, "type": PB_QUEUE_PARASITE,
                    "detail": "la valeur déborde sur la phrase suivante"}
        if field in MAX_LEN and len(value) > MAX_LEN[field]:
            return {"champ": field, "type": PB_TROP_LONG,
                    "detail": f"{len(value)} caractères (> {MAX_LEN[field]})"}
    return None


def decide(extracted: dict, flat_text: str, fields: Optional[list] = None) -> dict:
    """Décision de routage vers l'API.

    Retourne {needs_llm, problemes:[{champ,type,detail}], champs_a_reprendre:[...]}.
    `fields` = champs à examiner (par défaut, les clés métier de `extracted`)."""
    folded = _fold(flat_text)
    if fields is None:
        fields = [k for k in extracted.keys()
                  if not k.startswith("_") and k != "flat_text"]
    problemes = []
    for field in fields:
        pb = analyser_champ(field, extracted.get(field), folded)
        if pb:
            problemes.append(pb)
    champs = sorted({p["champ"] for p in problemes})
    return {
        "needs_llm": bool(problemes),
        "problemes": problemes,
        "champs_a_reprendre": champs,
    }


def hint_pour_llm(decision: dict) -> str:
    """Transforme la décision en consigne ciblée à injecter dans le prompt LLM
    (via le feedback_hint de la Couche 4)."""
    if not decision.get("problemes"):
        return ""
    lignes = ["Champs à reprendre en priorité (repérés par le modèle de décision) :"]
    for p in decision["problemes"]:
        lignes.append(f"- {p['champ']} : {p['detail']}")
    lignes.append("Classe correctement les valeurs multiples (ex. montant HTVA vs TTC).")
    return "\n".join(lignes)


def refine_if_needed(extracted: dict, flat_text: str, section: str = "AVIS",
                     providers: Optional[list] = None):
    """Routage complet demandé par l'utilisateur :
      1re passe (déjà dans `extracted`) -> DÉCISION -> si problème, API (Couche 4).

    La Couche 4 reçoit une consigne ciblée (hint_pour_llm) pour corriger/classer
    précisément les champs fautifs (ex. montant HTVA vs TTC). Retourne
    (resultat, decision). Si rien à reprendre, renvoie l'extraction telle quelle."""
    decision = decide(extracted, flat_text)
    if not decision["needs_llm"]:
        return extracted, decision
    from layer4_review import review_and_complete  # import tardif : évite le cycle
    refined = review_and_complete(
        flat_text, extracted, section,
        feedback_hint=hint_pour_llm(decision), providers=providers,
    )
    return (refined or extracted), decision


if __name__ == "__main__":
    import json
    import sys
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    avis = data["AVIS"] if isinstance(data, dict) else data
    n_llm = 0
    for a in avis:
        d = decide(a, a.get("flat_text", "") or " ".join(str(v) for v in a.values()))
        if d["needs_llm"]:
            n_llm += 1
    print(f"{n_llm}/{len(avis)} annonces seraient renvoyées à l'API par le modèle de décision")
