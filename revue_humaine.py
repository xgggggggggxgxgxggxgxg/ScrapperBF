"""
REVUE HUMAINE — file d'attente + ré-entrée des corrections.

Quand le juge classe une annonce 🟡 (petite file) ou 🔴 (alerte), on écrit un
DOSSIER contenant TOUT ce qu'il faut à l'humain pour trancher : le texte, les
deux extractions A et B, les champs en conflit, le score, et le POURQUOI de la
décision (y compris un éventuel désaccord entre le juge-LLM et le scoreur).

L'humain répond en DEUX champs par correction :
  - `quoi_faire`       : la consigne (« séparer HTVA et TTC », « relire la date »…)
  - `valeur_correcte`  : la valeur qui aurait dû être extraite

`appliquer_correction()` applique ces deux champs ET crée une RÈGLE (regles.py)
pour que les extractions futures évitent l'erreur (agit sur le prompt amont).

Stockage simple en fichiers JSON sous output/revue_humaine/. Pur / hors-ligne.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import regles

log = logging.getLogger("scrapperbf.revue")

REVUE_DIR = Path(__file__).parent / "output" / "revue_humaine"


def _dir(base: Optional[Path] = None) -> Path:
    d = Path(base) if base else REVUE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def creer_dossier(decision: dict, text: str, numero: str, index: int,
                  section: str = "AVIS", base: Optional[Path] = None) -> dict:
    """Écrit un dossier de revue humaine à partir d'une décision du juge.
    Retourne le dossier (avec son chemin)."""
    d = _dir(base)
    dossier = {
        "id": f"{numero}-{section}-{index}",
        "numero": numero,
        "section": section,
        "cree_le": datetime.now().isoformat(timespec="seconds"),
        "statut": "en_attente",             # en_attente -> corrige
        "couleur": decision.get("couleur"),
        "priorite": decision.get("couleur"),  # jaune (basse) / rouge (haute)
        "raison": decision.get("raison"),
        "pourquoi_divergence_juges": decision.get("pourquoi_divergence_juges"),
        "score": decision.get("score"),
        "verdict_scoreur": decision.get("verdict_scoreur"),
        "verdict_llm": decision.get("verdict_llm"),
        "champs_en_conflit": decision.get("champs_en_conflit", []),
        "divergences": decision.get("divergences", []),
        "analyse_llm": decision.get("analyse_llm", ""),
        "extraction_a": decision.get("extraction_a"),
        "extraction_b": decision.get("extraction_b"),
        "champs_proposes": decision.get("champs_finaux", {}),
        "texte": text,
        "corrections": [],                  # rempli par appliquer_correction
    }
    path = d / f"{dossier['id']}.json"
    path.write_text(json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8")
    dossier["_path"] = str(path)
    log.info("dossier revue humaine %s (%s) -> %s", dossier["id"], dossier["couleur"], path)
    return dossier


def appliquer_correction(dossier_id: str, champ: str, quoi_faire: str,
                         valeur_correcte, declencheurs: Optional[list] = None,
                         base: Optional[Path] = None,
                         regles_path: Optional[Path] = None) -> dict:
    """Applique la correction humaine (2 champs : quoi_faire + valeur_correcte),
    marque le dossier comme corrigé, et CRÉE une règle réutilisable.

    Retourne le dossier mis à jour. La valeur corrigée est destinée à remplacer
    `champ` dans l'enregistrement final (fait par l'appelant qui réinjecte)."""
    d = _dir(base)
    path = d / f"{dossier_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"dossier introuvable : {path}")
    dossier = json.loads(path.read_text(encoding="utf-8"))

    correction = {
        "champ": champ,
        "quoi_faire": quoi_faire,
        "valeur_correcte": valeur_correcte,
        "corrige_le": datetime.now().isoformat(timespec="seconds"),
    }
    dossier["corrections"].append(correction)
    dossier["champs_proposes"][champ] = valeur_correcte  # valeur finale retenue
    dossier["statut"] = "corrige"
    path.write_text(json.dumps(dossier, ensure_ascii=False, indent=2), encoding="utf-8")

    # Apprentissage : la correction devient une règle par type de situation.
    regle = regles.add_rule(
        section=dossier.get("section", "AVIS"),
        champ=champ,
        situation=quoi_faire,
        quoi_faire=quoi_faire,
        declencheurs=declencheurs or [],
        exemple_valeur=valeur_correcte,
        source=f"correction humaine (dossier {dossier_id})",
        path=regles_path,
    )
    dossier["_regle_creee"] = regle.get("id")
    log.info("dossier %s corrigé (champ %s) -> règle %s", dossier_id, champ, regle.get("id"))
    return dossier


def lister(statut: Optional[str] = None, base: Optional[Path] = None) -> list:
    """Liste les dossiers (optionnellement filtrés par statut)."""
    d = _dir(base)
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            dos = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if statut is None or dos.get("statut") == statut:
            dos["_path"] = str(p)
            out.append(dos)
    return out


if __name__ == "__main__":
    for dos in lister("en_attente"):
        print(f"[{dos['couleur']}] {dos['id']} — {dos['raison']}")
