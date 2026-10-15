"""
Recherche d'avis par mots-clés (ce que fait un lecteur : "montre-moi tous les
avis qui parlent d'informatique à Ouagadougou").

- insensible à la casse ET aux accents (indispensable en français) ;
- multi mots-clés en ET par défaut (tous les mots doivent apparaître),
  OU optionnel ;
- pondération par champ : un mot dans l'OBJET pèse plus que dans le financement ;
- renvoie un score, les champs qui ont matché et un extrait (snippet).

Sert aussi de brique à la plateforme (même logique de normalisation) et de
simulateur : `simulate()` rejoue une batterie de recherches et résume les
résultats, pour vérifier la pertinence sur un vrai jeu de données.
"""

import re
import sys
import json
import unicodedata
from pathlib import Path
from typing import Optional

# Champs fouillés et leur poids relatif dans le score de pertinence.
FIELD_WEIGHTS = {
    "objet": 3.0,
    "autorite_emettrice": 2.0,
    "type_avis": 1.0,
    "categorie": 1.0,
    "numero_reference": 1.0,
    "financement": 1.0,
    "delai_execution": 0.5,
    "date_limite_depot": 0.5,
}
# Mots vides ignorés dans les requêtes (n'apportent rien à la recherche).
STOPWORDS = {"de", "des", "du", "la", "le", "les", "et", "a", "au", "aux",
             "en", "un", "une", "pour", "par", "d", "l", "sur"}


def fold(s) -> str:
    """minuscule + sans accents + espaces normalisés."""
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower()).strip()


def tokenize(query: str) -> list:
    """Découpe une requête en mots-clés normalisés, sans mots vides."""
    toks = re.findall(r"[0-9a-zÀ-ÿ]+", fold(query))
    return [t for t in toks if t and t not in STOPWORDS]


def _haystacks(avis: dict) -> dict:
    """Pour chaque champ pondéré, le texte normalisé où chercher."""
    out = {}
    for field in FIELD_WEIGHTS:
        v = avis.get(field)
        if isinstance(v, dict):
            v = " ".join(str(x) for x in v.values() if x)
        elif isinstance(v, list):
            v = " ".join(str(x) for x in v)
        out[field] = fold(v)
    return out


def score_avis(avis: dict, tokens: list, mode: str = "and"):
    """Retourne (score, champs_matchés, mots_trouvés). score=0 si l'annonce ne
    satisfait pas le mode (ET = tous les mots ; OU = au moins un)."""
    hay = _haystacks(avis)
    score = 0.0
    matched_fields = set()
    found_tokens = set()
    for tok in tokens:
        hit_this_token = False
        for field, weight in FIELD_WEIGHTS.items():
            if tok in hay[field]:
                score += weight
                matched_fields.add(field)
                hit_this_token = True
        if hit_this_token:
            found_tokens.add(tok)
    if not tokens:
        return 0.0, [], []
    if mode == "and" and len(found_tokens) < len(tokens):
        return 0.0, [], []          # il manque au moins un mot-clé
    if mode == "or" and not found_tokens:
        return 0.0, [], []
    # Bonus : tous les mots présents dans l'objet = très pertinent.
    if all(tok in hay["objet"] for tok in tokens):
        score += 2.0
    return score, sorted(matched_fields), sorted(found_tokens)


def search(avis_list: list, query: str, mode: str = "and", limit: Optional[int] = None) -> list:
    """Recherche `query` dans une liste d'avis. Renvoie les résultats triés par
    pertinence décroissante : [{avis, score, champs, mots, snippet}]."""
    tokens = tokenize(query)
    results = []
    for a in avis_list:
        score, fields, found = score_avis(a, tokens, mode)
        if score > 0:
            results.append({
                "avis": a,
                "score": round(score, 1),
                "champs": fields,
                "mots": found,
                "snippet": _snippet(a, tokens),
            })
    results.sort(key=lambda r: (-r["score"], str(r["avis"].get("objet") or "")))
    return results[:limit] if limit else results


def _snippet(avis: dict, tokens: list, width: int = 90) -> str:
    """Extrait de l'objet autour du premier mot-clé trouvé."""
    objet = avis.get("objet") or avis.get("autorite_emettrice") or ""
    folded = fold(objet)
    pos = min((folded.find(t) for t in tokens if t in folded), default=-1)
    if pos < 0:
        return objet[:width]
    start = max(0, pos - width // 3)
    return ("…" if start else "") + objet[start:start + width].strip() + "…"


def simulate(avis_list: list, queries: list, mode: str = "and", top: int = 3) -> dict:
    """Rejoue une série de recherches (comme un utilisateur) et renvoie un
    résumé : par requête, le nombre de résultats et les meilleurs."""
    report = {}
    for q in queries:
        res = search(avis_list, q, mode=mode)
        report[q] = {
            "nb": len(res),
            "top": [
                {
                    "bulletin": r["avis"].get("_source_pdf") or r["avis"].get("_numero"),
                    "page": r["avis"].get("_page_debut"),
                    "score": r["score"],
                    "objet": (r["avis"].get("objet") or "")[:80],
                }
                for r in res[:top]
            ],
        }
    return report


# Jeu de requêtes réalistes d'un lecteur de marchés publics BF.
DEMO_QUERIES = [
    "informatique",
    "construction",
    "véhicules",
    "manifestation d'intérêt",
    "fournitures de bureau",
    "travaux Ouagadougou",
    "consultant étude",
    "santé",
]


def _load(path: str) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data["AVIS"] if isinstance(data, dict) and "AVIS" in data else data


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python search_avis.py <dataset.json> <requête...>   # une recherche")
        print("  python search_avis.py <dataset.json> --simulate     # batterie de recherches")
        sys.exit(1)

    avis_list = _load(sys.argv[1])
    rest = sys.argv[2:]

    if not rest or rest[0] == "--simulate":
        print(f"Simulation de recherches sur {len(avis_list)} avis\n" + "=" * 54)
        rep = simulate(avis_list, DEMO_QUERIES)
        for q, info in rep.items():
            print(f"\n🔎 « {q} » → {info['nb']} résultat(s)")
            for t in info["top"]:
                print(f"    [{t['score']:>4}] {t['bulletin']} p{t['page']} — {t['objet']}")
    else:
        query = " ".join(rest)
        res = search(avis_list, query)
        print(f"« {query} » → {len(res)} résultat(s)\n" + "=" * 54)
        for r in res[:15]:
            a = r["avis"]
            print(f"[{r['score']:>4}] {a.get('_source_pdf')} p{a.get('_page_debut')} "
                  f"| {a.get('categorie') or '—'}\n       {r['snippet']}\n"
                  f"       champs: {', '.join(r['champs'])}")
