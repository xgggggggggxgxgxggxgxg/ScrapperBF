"""Couche 4 — pipeline complet, validation, tracking et contrôle qualité.

Enchaîne :

    Markdown → pages numérotées → annonces triées par titre → extraction LLM
             → contrôle qualité → rapport + dashboard de vérification humaine

Deux propriétés sont conservées de la version initiale, parce qu'elles portent
tout le backfill :

* **idempotence** — un document traité avec succès n'est jamais retraité
  (registre SQLite ``ingestion.db``), donc le backfill se relance sans
  dupliquer ni le travail ni les coûts d'API ;
* **reprise fine** — ``reprocess_failures()`` rejoue les seules unités en
  échec, après un ajustement de schéma ou un changement de provider.

S'y ajoute le contrôle qualité : chaque annonce extraite est notée, les
problèmes sont écrits en base avec leurs références de page, et un dashboard
HTML est produit pour la relecture humaine.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import yaml

from layer3_extract_llm import extract_unit
from scrapperbf.core.pages import parse_file
from scrapperbf.core.segment import segment
from scrapperbf.dashboard.build import write_dashboard
from scrapperbf.feedback.store import FeedbackStore
from scrapperbf.qa.report import audit_document

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "ingestion.db"
OUTPUT_DIR = BASE_DIR / "output"
SCHEMA_PATH = BASE_DIR / "schema_config.yaml"
OUTPUT_DIR.mkdir(exist_ok=True)


def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    # Résolu à l'appel, pas à la définition : une valeur par défaut capturerait
    # DB_PATH une fois pour toutes et ignorerait toute reconfiguration
    # (tests, exécution sur un autre répertoire de travail).
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            numero TEXT PRIMARY KEY,
            statut TEXT,              -- 'en_cours' | 'ok' | 'echec_partiel'
            date_traitement TEXT,
            nb_unites_ok INTEGER,
            nb_unites_echec INTEGER
        );
        CREATE TABLE IF NOT EXISTS unites_echouees (
            numero TEXT,
            unit_id TEXT,
            section TEXT,
            kind TEXT,
            page_debut INTEGER,
            page_fin INTEGER,
            flat_text TEXT,
            motif TEXT
        );
        -- Résultat du contrôle qualité, conservé pour suivre la dérive de
        -- qualité d'un bulletin à l'autre et alimenter le dashboard.
        CREATE TABLE IF NOT EXISTS qualite_unites (
            numero TEXT,
            unit_id TEXT,
            kind TEXT,
            page_debut INTEGER,
            page_fin INTEGER,
            verdict TEXT,
            confiance REAL,
            completude REAL,
            exactitude REAL,
            structure REAL,
            nb_alertes INTEGER,
            champs_critiques_manquants TEXT,
            PRIMARY KEY (numero, unit_id)
        );
        CREATE TABLE IF NOT EXISTS alertes (
            numero TEXT,
            unit_id TEXT,
            code TEXT,
            gravite INTEGER,
            pages TEXT,
            message TEXT,
            champ TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_alertes_numero ON alertes(numero);
        CREATE INDEX IF NOT EXISTS idx_qualite_verdict ON qualite_unites(verdict);
        """
    )
    conn.commit()
    return conn


def already_processed(conn: sqlite3.Connection, numero: str) -> bool:
    row = conn.execute("SELECT statut FROM documents WHERE numero=?", (numero,)).fetchone()
    return row is not None and row[0] == "ok"


def _schema() -> dict:
    # Chargé une fois par exécution : la version initiale relisait et parsait le
    # YAML pour chaque unité validée.
    if not hasattr(_schema, "_cache"):
        _schema._cache = yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8")) or {}
    return _schema._cache


def is_valid(extracted: dict, section: str) -> bool:
    """Validation minimale : tous les champs ``required`` du YAML sont remplis.

    Rejette les extractions creuses plutôt que de polluer la base. Le jugement
    nuancé (complétude, exactitude, ancrage) est l'affaire du contrôle qualité ;
    ici on ne filtre que ce qui est inutilisable par construction.
    """
    for field, spec in (_schema().get(section) or {}).items():
        if (spec or {}).get("required") and not extracted.get(field):
            return False
    return True


def process_document(
    md_path: str,
    numero: str,
    sections: tuple[str, ...] = ("AVIS", "RESULTATS"),
    use_feedback: bool = True,
    dashboard: bool = True,
    force: bool = False,
) -> dict:
    """Traite un bulletin de bout en bout et retourne les statistiques QA."""
    conn = init_db()
    if not force and already_processed(conn, numero):
        print(f"[SKIP] {numero} déjà traité avec succès.")
        conn.close()
        return {"skipped": True}

    conn.execute(
        "INSERT OR REPLACE INTO documents VALUES (?, 'en_cours', ?, 0, 0)",
        (numero, datetime.now().isoformat()),
    )
    conn.commit()

    # Couches 1 + 2 : déterministes, aucun appel API — relançables sans coût.
    doc = parse_file(md_path)
    notices = segment(doc)
    store = FeedbackStore() if use_feedback else None

    results: dict[str, list[dict]] = {section: [] for section in sections}
    extractions: dict[str, dict] = {}
    nb_ok = nb_echec = 0

    conn.execute("DELETE FROM unites_echouees WHERE numero=?", (numero,))

    for notice in notices:
        if notice.section not in sections:
            continue

        # Les retours humains sur ce type d'annonce sont injectés dans le
        # prompt : les champs déjà corrigés à la main sont signalés au modèle.
        hints = store.prompt_supplement(notice.kind.value) if store else ""
        extracted = extract_unit(notice.flat_text, notice.section, extra_instructions=hints)

        if extracted is None or not is_valid(extracted, notice.section):
            nb_echec += 1
            motif = "aucune réponse du modèle" if extracted is None else "champs requis absents"
            conn.execute(
                "INSERT INTO unites_echouees VALUES (?,?,?,?,?,?,?,?)",
                (numero, notice.id, notice.section, notice.kind.value,
                 notice.page_debut, notice.page_fin, notice.flat_text, motif),
            )
            continue

        # Métadonnées de segmentation : clés alignées sur ce que produit la
        # segmentation (la version initiale lisait `avis_category`/`avis_type`,
        # jamais écrites, donc ces champs sortaient toujours vides).
        extracted["_unit_id"] = notice.id
        extracted["_page_debut"] = notice.page_debut
        extracted["_page_fin"] = notice.page_fin
        extracted["_nature"] = notice.kind.value
        if notice.section == "AVIS":
            extracted["categorie_emettrice"] = notice.category
            extracted.setdefault("categorie", notice.marche_type)

        results[notice.section].append(extracted)
        extractions[notice.id] = extracted
        nb_ok += 1

    conn.commit()

    out_file = OUTPUT_DIR / f"{numero}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    report = audit_document(doc, numero, extractions=extractions, store=store)
    _persist_quality(conn, numero, report)

    report_file = OUTPUT_DIR / f"{numero}_qa.json"
    report_file.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    statut = "ok" if nb_echec == 0 else "echec_partiel"
    conn.execute(
        "UPDATE documents SET statut=?, nb_unites_ok=?, nb_unites_echec=? WHERE numero=?",
        (statut, nb_ok, nb_echec, numero),
    )
    conn.commit()
    conn.close()

    print(f"[{numero}] {nb_ok} unités OK, {nb_echec} échecs -> {out_file}")
    print(report.to_text(max_alerts=15))
    print(f"\nRapport QA -> {report_file}")
    if dashboard:
        path = write_dashboard(report, OUTPUT_DIR / f"{numero}_dashboard.html")
        print(f"Dashboard  -> {path}")

    return report.stats


def _persist_quality(conn: sqlite3.Connection, numero: str, report) -> None:
    """Écrit notes et alertes en base, en remplaçant celles du même bulletin."""
    conn.execute("DELETE FROM qualite_unites WHERE numero=?", (numero,))
    conn.execute("DELETE FROM alertes WHERE numero=?", (numero,))

    for audit in report.notices:
        score = audit.score
        conn.execute(
            "INSERT INTO qualite_unites VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (numero, audit.notice.id, audit.notice.kind.value,
             audit.notice.page_debut, audit.notice.page_fin,
             score.verdict.value, round(score.confidence, 4),
             round(score.completeness, 4), round(score.accuracy, 4), round(score.structure, 4),
             len(audit.alerts), ", ".join(score.missing_critical)),
        )
    for alert in report.all_alerts:
        conn.execute(
            "INSERT INTO alertes VALUES (?,?,?,?,?,?,?)",
            (numero, alert.unit_id, alert.code, int(alert.severity),
             json.dumps(sorted(set(alert.page_refs))), alert.message, alert.field_name),
        )
    conn.commit()


def reprocess_failures(numero: str, use_feedback: bool = True) -> int:
    """Rejoue les unités en échec d'un document, sans toucher aux unités OK."""
    conn = init_db()
    rows = conn.execute(
        "SELECT rowid, unit_id, section, kind, flat_text FROM unites_echouees WHERE numero=?",
        (numero,),
    ).fetchall()
    store = FeedbackStore() if use_feedback else None
    out_file = OUTPUT_DIR / f"{numero}.json"
    recovered = 0

    for rowid, unit_id, section, kind, flat_text in rows:
        hints = store.prompt_supplement(kind) if store else ""
        extracted = extract_unit(flat_text, section, extra_instructions=hints)
        if not extracted or not is_valid(extracted, section):
            continue

        extracted["_unit_id"] = unit_id
        data = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else {}
        data.setdefault(section, []).append(extracted)
        out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        conn.execute("DELETE FROM unites_echouees WHERE rowid=?", (rowid,))
        recovered += 1

    remaining = conn.execute(
        "SELECT COUNT(*) FROM unites_echouees WHERE numero=?", (numero,)
    ).fetchone()[0]
    if remaining == 0:
        conn.execute("UPDATE documents SET statut='ok' WHERE numero=?", (numero,))
    conn.execute(
        "UPDATE documents SET nb_unites_echec=? WHERE numero=?", (remaining, numero)
    )
    conn.commit()
    conn.close()
    print(f"[{numero}] {recovered} unité(s) récupérée(s), {remaining} encore en échec.")
    return recovered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pipeline d'extraction + contrôle qualité")
    parser.add_argument("markdown", nargs="?", default=str(BASE_DIR / "quotidien_4466.md"))
    parser.add_argument("numero", nargs="?", default="N4466")
    parser.add_argument("--sections", default="AVIS,RESULTATS",
                        help="Sections à extraire (AVIS, RESULTATS)")
    parser.add_argument("--no-feedback", action="store_true",
                        help="Ne pas injecter les retours humains dans le prompt")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--force", action="store_true", help="Retraiter même si déjà réussi")
    parser.add_argument("--reprocess-failures", action="store_true",
                        help="Rejouer uniquement les unités en échec")
    args = parser.parse_args(argv)

    if args.reprocess_failures:
        reprocess_failures(args.numero, use_feedback=not args.no_feedback)
        return 0

    process_document(
        args.markdown,
        args.numero,
        sections=tuple(s.strip() for s in args.sections.split(",") if s.strip()),
        use_feedback=not args.no_feedback,
        dashboard=not args.no_dashboard,
        force=args.force,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
