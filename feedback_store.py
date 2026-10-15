"""
Magasin de rétroaction (boucle d'amélioration).

Chaque fois qu'une annonce obtient un score d'exactitude < 100 %, ses RAISONS
d'écart (champ par champ) sont enregistrées ici. Au traitement suivant, on
agrège les problèmes récurrents par champ et on les réinjecte dans le prompt
de révision (`feedback_hint`) : le modèle voit explicitement ses erreurs
passées et évite de les reproduire.

Stockage volontairement simple (JSON local, non versionné). La fonction
`build_hint` ne dépend que de ce fichier -> testable hors-ligne.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Optional, Union

DEFAULT_PATH = Path(__file__).parent / "feedback_store.json"


def _load(path: Union[str, Path]) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data
    except (FileNotFoundError, ValueError, OSError):
        pass
    return {"entries": []}


def record(reasons: list, numero: str, section: str,
           score: Optional[int] = None,
           path: Union[str, Path] = DEFAULT_PATH) -> None:
    """Enregistre les raisons d'écart d'une annonce. No-op si pas de raison."""
    if not reasons:
        return
    data = _load(path)
    data["entries"].append({
        "numero": numero,
        "section": section,
        "score": score,
        "raisons": reasons,
    })
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def build_hint(section: str, path: Union[str, Path] = DEFAULT_PATH,
               max_items: int = 6) -> str:
    """Construit le rappel injecté dans le prompt de révision : les champs les
    plus souvent problématiques dans les runs passés, avec un exemple de raison.
    Chaîne vide si aucun historique -> aucun bruit dans le prompt."""
    data = _load(path)
    counter: Counter = Counter()
    exemple = {}
    for entry in data["entries"]:
        if entry.get("section") != section:
            continue
        for r in entry.get("raisons", []):
            if not isinstance(r, dict):
                continue
            champ = r.get("champ")
            if not champ:
                continue
            counter[champ] += 1
            exemple.setdefault(champ, (r.get("raison") or "").strip())

    if not counter:
        return ""

    lignes = []
    for champ, n in counter.most_common(max_items):
        detail = exemple.get(champ) or "imprécisions constatées"
        fois = "fois" if n > 1 else "fois"
        lignes.append(f"- {champ} (problème rencontré {n} {fois}) : {detail}")
    return "\n".join(lignes)


def summary(path: Union[str, Path] = DEFAULT_PATH) -> dict:
    """Vue d'ensemble : nb d'annonces en écart et classement des champs fautifs.
    Utile pour un tableau de bord qualité ou un rapport périodique."""
    data = _load(path)
    counter: Counter = Counter()
    for entry in data["entries"]:
        for r in entry.get("raisons", []):
            if isinstance(r, dict) and r.get("champ"):
                counter[r["champ"]] += 1
    return {
        "nb_annonces_en_ecart": len(data["entries"]),
        "champs_les_plus_fautifs": counter.most_common(),
    }


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    print(json.dumps(summary(p), ensure_ascii=False, indent=2))
