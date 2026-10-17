"""
RÈGLES par type de situation — mémoire d'apprentissage du pipeline.

Remplace l'ancienne « boucle de rétroaction » : au lieu de réinjecter des
statistiques d'erreurs, on stocke des RÈGLES explicites, créées à partir des
corrections humaines (« quand telle situation se présente, faire ceci »), et on
les réinjecte dans le prompt des DEUX extracteurs (en amont).

Une règle :
  {
    "id": "r3",
    "section": "AVIS",
    "champ": "montant_previsionnel_global",   # champ concerné (indicatif)
    "situation": "montant HTVA et TTC collés dans la même valeur",
    "declencheurs": ["htva", "ttc"],           # sous-chaînes (accents/casse ignorés)
    "quoi_faire": "séparer le montant HTVA du montant TTC dans deux sous-champs",
    "exemple_valeur": {"htva": "80 000 000", "ttc": "94 400 000"},
    "source": "correction humaine (dossier N4503-2)",
    "cree_le": "2026-10-17T..."
  }

Matching : une règle s'applique si TOUS ses `declencheurs` sont présents dans le
texte (normalisé). Une règle sans `declencheurs` est GLOBALE pour sa section.

Pur / sans réseau / testable hors-ligne. Stockage = regles.json (gitignoré si tu
veux le garder local ; ici versionné vide par défaut).
"""

import json
import logging
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("scrapperbf.regles")

REGLES_PATH = Path(__file__).parent / "regles.json"


def _fold(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def load_rules(path: Optional[Path] = None) -> list:
    """Charge toutes les règles (liste). [] si le fichier est absent/illisible."""
    p = Path(path or REGLES_PATH)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("regles", [])
    except Exception as e:
        log.warning("regles.json illisible (%s) -> aucune règle", e)
        return []


def save_rules(rules: list, path: Optional[Path] = None) -> None:
    p = Path(path or REGLES_PATH)
    p.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")


def match_rules(text: str, section: str, rules: Optional[list] = None) -> list:
    """Règles applicables à CE texte pour CETTE section : déclencheurs tous
    présents (ou règle globale sans déclencheur)."""
    rules = rules if rules is not None else load_rules()
    ft = _fold(text)
    out = []
    for r in rules:
        if r.get("section") and r["section"] != section:
            continue
        decl = [_fold(d) for d in (r.get("declencheurs") or []) if str(d).strip()]
        if all(d in ft for d in decl):   # all([]) == True -> règle globale
            out.append(r)
    return out


def format_for_prompt(rules: list) -> str:
    """Bloc texte à injecter dans le prompt des extracteurs. Vide si aucune règle
    (l'appelant n'ajoute alors rien)."""
    if not rules:
        return ""
    lignes = ["RÈGLES APPRISES (corrections passées à respecter impérativement) :"]
    for i, r in enumerate(rules, 1):
        bout = f"{i}. [{r.get('champ', '—')}] {r.get('situation', '')} -> {r.get('quoi_faire', '')}"
        ex = r.get("exemple_valeur")
        if ex is not None:
            bout += f" (ex. attendu : {json.dumps(ex, ensure_ascii=False)})"
        lignes.append(bout.strip())
    return "\n".join(lignes)


def add_rule(section: str, champ: str, situation: str, quoi_faire: str,
             declencheurs: Optional[list] = None, exemple_valeur=None,
             source: str = "", path: Optional[Path] = None) -> dict:
    """Ajoute une règle et la persiste. Déduplique sur (section, champ, situation)
    pour ne pas empiler la même leçon. Retourne la règle créée (ou existante)."""
    rules = load_rules(path)
    key = (section, champ, _fold(situation))
    for r in rules:
        if (r.get("section"), r.get("champ"), _fold(r.get("situation"))) == key:
            return r  # déjà connue
    regle = {
        "id": f"r{len(rules) + 1}",
        "section": section,
        "champ": champ,
        "situation": situation,
        "declencheurs": declencheurs or [],
        "quoi_faire": quoi_faire,
        "exemple_valeur": exemple_valeur,
        "source": source,
        "cree_le": datetime.now().isoformat(timespec="seconds"),
    }
    rules.append(regle)
    save_rules(rules, path)
    log.info("nouvelle règle %s [%s] : %s", regle["id"], champ, situation)
    return regle


if __name__ == "__main__":
    rules = load_rules()
    print(f"{len(rules)} règle(s) dans {REGLES_PATH.name}\n")
    print(format_for_prompt(rules) or "(aucune)")
