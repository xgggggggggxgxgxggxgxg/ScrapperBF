"""
Pipeline complet + Couche 4 (validation/fallback) + tracking pour :
  - exécution hebdomadaire (nouveau numéro de la revue)
  - backfill (1 mois, puis 2 ans si concluant)

Idée clé pour le backfill : ne JAMAIS retraiter un fichier déjà traité avec succès.
On garde un registre (SQLite) : numero_revue -> statut, date, nb_unites_ok, nb_echecs.
Cela permet de relancer le backfill autant de fois que nécessaire sans dupliquer
le travail ni les coûts API, et de reprendre après une coupure au bon endroit.
"""

import os
import sys
import sqlite3
import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent

# layer2_segment vit dans new_logic/ : on l'ajoute au path pour que l'import
# fonctionne que le pipeline soit lancé comme script ou importé comme module.
_NEW_LOGIC = BASE_DIR / "new_logic"
if _NEW_LOGIC.is_dir() and str(_NEW_LOGIC) not in sys.path:
    sys.path.insert(0, str(_NEW_LOGIC))

from layer1_extract import extract_document_from_markdown as extract_document
from layer2_segment import segment_document, unit_to_text
from layer3_extract_llm import extract_unit
from layer4_review import review_and_complete, _is_empty

DB_PATH = BASE_DIR / "ingestion.db"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Couche 4 (auto-analyse + auto-complétion) activée par défaut ; désactivable
# (ENABLE_REVIEW=0) pour un run purement déterministe/première passe.
ENABLE_REVIEW = os.environ.get("ENABLE_REVIEW", "1") not in ("0", "false", "False", "")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            numero TEXT PRIMARY KEY,
            statut TEXT,              -- 'en_cours' | 'ok' | 'echec_partiel'
            date_traitement TEXT,
            nb_unites_ok INTEGER,
            nb_unites_echec INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unites_echouees (
            numero TEXT,
            section TEXT,
            page_debut INTEGER,
            flat_text TEXT
        )
    """)
    conn.commit()
    return conn


def already_processed(conn, numero: str) -> bool:
    row = conn.execute("SELECT statut FROM documents WHERE numero=?", (numero,)).fetchone()
    return row is not None and row[0] == "ok"


def process_document(md_path: str, numero: str):
    """
    numero = identifiant unique du document (ex: 'N4466' extrait du nom de fichier
    ou du contenu). Sert de clé d'idempotence pour le backfill.
    """
    conn = init_db()

    if already_processed(conn, numero):
        print(f"[SKIP] {numero} déjà traité avec succès.")
        return

    conn.execute(
        "INSERT OR REPLACE INTO documents VALUES (?, 'en_cours', ?, 0, 0)",
        (numero, datetime.now().isoformat()),
    )
    conn.commit()

    # Couches 1 + 2 (déterministes, pas d'appel API, donc pas de coût à relancer)
    pages = extract_document(md_path)
    sections = segment_document(pages)

    results = {"AVIS": []}
    nb_ok, nb_echec = 0, 0

    for section in ("AVIS",):
        for unit in sections.get(section, []):
            flat_text = unit_to_text(unit)

            extracted = extract_unit(flat_text, section)  # Couche 3

            if extracted is not None:
                # Valeurs déterministes de la segmentation injectées AVANT la
                # révision, pour que l'auto-complétion les confirme au lieu de
                # les redécouvrir (et ne les laisse jamais vides).
                if section == "AVIS":
                    if _is_empty(extracted.get("categorie")):
                        extracted["categorie"] = unit.get("type_marche")
                    extracted["_categorie_autorite"] = unit.get("categorie")

                # Couche 4 : auto-analyse + auto-complétion de TOUTES les variables.
                if ENABLE_REVIEW:
                    reviewed = review_and_complete(flat_text, extracted, section)
                    if reviewed is not None:
                        extracted = reviewed

            # Validation minimale + fallback
            if extracted is None or not _is_valid(extracted, section):
                nb_echec += 1
                conn.execute(
                    "INSERT INTO unites_echouees VALUES (?, ?, ?, ?)",
                    (numero, section, unit["page_debut"], flat_text),
                )
                continue

            extracted["_page_debut"] = unit["page_debut"]
            extracted["_page_fin"] = unit["page_fin"]
            results[section].append(extracted)
            nb_ok += 1

    conn.commit()

    out_file = OUTPUT_DIR / f"{numero}.json"
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    statut = "ok" if nb_echec == 0 else "echec_partiel"
    conn.execute(
        "UPDATE documents SET statut=?, nb_unites_ok=?, nb_unites_echec=? WHERE numero=?",
        (statut, nb_ok, nb_echec, numero),
    )
    conn.commit()
    conn.close()

    print(f"[{numero}] {nb_ok} unités OK, {nb_echec} échecs -> {out_file}")


def _is_valid(extracted: dict, section: str) -> bool:
    """Validation minimale : les champs marqués 'required' dans le YAML doivent
    être présents et non vides. Rejette silencieusement les extractions creuses
    plutôt que de polluer la base avec du bruit."""
    import yaml
    cfg = yaml.safe_load((Path(__file__).parent / "schema_config.yaml").read_text(encoding="utf-8"))
    for field, spec in cfg[section].items():
        if spec.get("required") and not extracted.get(field):
            return False
    return True


def reprocess_failures(numero: str):
    """Relance uniquement les unités échouées d'un document (utile après avoir
    ajusté le schéma ou changé de provider), sans retoucher aux unités déjà OK."""
    conn = init_db()
    rows = conn.execute(
        "SELECT rowid, section, flat_text FROM unites_echouees WHERE numero=?", (numero,)
    ).fetchall()
    for rowid, section, flat_text in rows:
        extracted = extract_unit(flat_text, section)
        if extracted is not None and ENABLE_REVIEW:
            reviewed = review_and_complete(flat_text, extracted, section)
            if reviewed is not None:
                extracted = reviewed
        if extracted and _is_valid(extracted, section):
            conn.execute("DELETE FROM unites_echouees WHERE rowid=?", (rowid,))
            # append au fichier de sortie existant
            out_file = OUTPUT_DIR / f"{numero}.json"
            data = json.loads(out_file.read_text(encoding="utf-8"))
            data[section].append(extracted)
            out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import sys
    md_path = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "quotidien_4466.md")
    numero = sys.argv[2] if len(sys.argv) > 2 else "N4466"
    process_document(md_path, numero)
