"""Mémoire des retours humains, et réinjection dans l'extraction.

Le dashboard ne sert à rien si ce que l'opérateur y corrige meurt à l'écran.
Ce module stocke trois choses, et les rend exploitables par le LLM :

* **revues** — le verdict humain sur une annonce, comparé à celui de la
  machine : c'est la seule mesure honnête de la qualité du scoring ;
* **corrections de champ** — valeur extraite vs valeur réelle. Agrégées par
  nature d'annonce et par champ, elles désignent les faiblesses récurrentes et
  fournissent des exemples concrets à mettre dans le prompt ;
* **jugements d'alerte** — « faux positif » / « confirmé ». Un code d'alerte
  massivement jugé faux positif doit être calmé, pas subi : sans cette
  statistique, l'opérateur finit par ignorer toutes les alertes.

Le stockage est un SQLite local, cohérent avec ``ingestion.db`` du pipeline :
aucun service à déployer pour que la boucle de retour fonctionne.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "feedback.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    kind TEXT,
    pages TEXT,
    verdict_machine TEXT,
    verdict_human TEXT NOT NULL,
    confidence_machine REAL,
    reviewer TEXT,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(document, unit_id, reviewer)
);
CREATE TABLE IF NOT EXISTS corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    kind TEXT,
    field TEXT NOT NULL,
    value_machine TEXT,
    value_human TEXT,
    reviewer TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alert_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document TEXT NOT NULL,
    unit_id TEXT,
    code TEXT NOT NULL,
    judgement TEXT NOT NULL,   -- 'faux_positif' | 'confirme'
    reviewer TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_corrections_kind_field ON corrections(kind, field);
CREATE INDEX IF NOT EXISTS idx_alert_feedback_code ON alert_feedback(code);
CREATE INDEX IF NOT EXISTS idx_reviews_document ON reviews(document);
"""

# En dessous de ce nombre d'observations, un taux n'est pas une information :
# 1 correction sur 1 annonce ne justifie pas de réécrire un prompt.
MIN_OBSERVATIONS = 3
FALSE_POSITIVE_MUTE_RATE = 0.6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


@dataclass
class FieldStat:
    """Fréquence des corrections sur un champ, pour une nature d'annonce."""

    field: str
    corrections: int
    examples: list[tuple[str, str]]

    def to_dict(self) -> dict:
        return {"field": self.field, "corrections": self.corrections, "examples": self.examples}


class FeedbackStore:
    """Accès au journal des retours humains."""

    def __init__(self, db_path: str | Path = DEFAULT_DB):
        self.db_path = str(db_path)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- écriture ----------------------------------------------------------

    def record_review(
        self,
        document: str,
        unit_id: str,
        verdict_human: str,
        kind: str | None = None,
        pages: list[int] | None = None,
        verdict_machine: str | None = None,
        confidence_machine: float | None = None,
        reviewer: str = "anonyme",
        note: str = "",
    ) -> None:
        """Enregistre le verdict d'un relecteur (remplace le sien s'il repasse)."""
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO reviews
                   (document, unit_id, kind, pages, verdict_machine, verdict_human,
                    confidence_machine, reviewer, note, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(document, unit_id, reviewer) DO UPDATE SET
                     verdict_human=excluded.verdict_human,
                     note=excluded.note,
                     created_at=excluded.created_at""",
                (document, unit_id, kind, json.dumps(pages or []), verdict_machine,
                 verdict_human, confidence_machine, reviewer, note, _now()),
            )
            conn.commit()

    def record_correction(
        self,
        document: str,
        unit_id: str,
        field: str,
        value_human: object,
        value_machine: object = None,
        kind: str | None = None,
        reviewer: str = "anonyme",
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO corrections
                   (document, unit_id, kind, field, value_machine, value_human, reviewer, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (document, unit_id, kind, field, _as_text(value_machine),
                 _as_text(value_human), reviewer, _now()),
            )
            conn.commit()

    def record_alert_feedback(
        self,
        document: str,
        code: str,
        judgement: str,
        unit_id: str | None = None,
        reviewer: str = "anonyme",
        note: str = "",
    ) -> None:
        if judgement not in ("faux_positif", "confirme"):
            raise ValueError("judgement doit valoir 'faux_positif' ou 'confirme'")
        with closing(self._connect()) as conn:
            conn.execute(
                """INSERT INTO alert_feedback
                   (document, unit_id, code, judgement, reviewer, note, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (document, unit_id, code, judgement, reviewer, note, _now()),
            )
            conn.commit()

    def import_reviews(self, payload: dict | list, reviewer: str = "anonyme") -> dict[str, int]:
        """Importe le fichier JSON exporté par le dashboard.

        Format attendu (ce que produit le bouton « Exporter les corrections ») :
        ``{"document": "N4466", "reviews": [{"unit_id": ..., "verdict": ...,
        "corrections": {champ: valeur}, "alerts": [{"code":..., "judgement":...}]}]}``
        """
        if isinstance(payload, list):
            payload = {"document": "inconnu", "reviews": payload}
        document = payload.get("document", "inconnu")
        counts = {"reviews": 0, "corrections": 0, "alertes": 0}
        for entry in payload.get("reviews", []):
            unit_id = entry.get("unit_id")
            if not unit_id:
                continue
            if entry.get("verdict"):
                self.record_review(
                    document=document,
                    unit_id=unit_id,
                    verdict_human=entry["verdict"],
                    kind=entry.get("kind"),
                    pages=entry.get("pages"),
                    verdict_machine=entry.get("verdict_machine"),
                    confidence_machine=entry.get("confidence_machine"),
                    reviewer=entry.get("reviewer", reviewer),
                    note=entry.get("note", ""),
                )
                counts["reviews"] += 1
            for field, value in (entry.get("corrections") or {}).items():
                self.record_correction(
                    document=document, unit_id=unit_id, field=field,
                    value_human=value, value_machine=(entry.get("extracted") or {}).get(field),
                    kind=entry.get("kind"), reviewer=entry.get("reviewer", reviewer),
                )
                counts["corrections"] += 1
            for alert in entry.get("alerts") or []:
                if alert.get("code") and alert.get("judgement"):
                    self.record_alert_feedback(
                        document=document, code=alert["code"], judgement=alert["judgement"],
                        unit_id=unit_id, reviewer=entry.get("reviewer", reviewer),
                        note=alert.get("note", ""),
                    )
                    counts["alertes"] += 1
        return counts

    # --- lecture -----------------------------------------------------------

    def field_stats(self, kind: str | None = None, limit: int = 8) -> list[FieldStat]:
        """Champs les plus corrigés, avec quelques exemples réels."""
        query = "SELECT field, COUNT(*) AS n FROM corrections"
        params: list[object] = []
        if kind:
            query += " WHERE kind = ?"
            params.append(kind)
        query += " GROUP BY field ORDER BY n DESC LIMIT ?"
        params.append(limit)

        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
            stats = []
            for row in rows:
                ex_query = "SELECT value_machine, value_human FROM corrections WHERE field = ?"
                ex_params: list[object] = [row["field"]]
                if kind:
                    ex_query += " AND kind = ?"
                    ex_params.append(kind)
                ex_query += " ORDER BY id DESC LIMIT 3"
                examples = [
                    (r["value_machine"], r["value_human"])
                    for r in conn.execute(ex_query, ex_params).fetchall()
                ]
                stats.append(FieldStat(row["field"], row["n"], examples))
        return stats

    def muted_alert_codes(self, min_observations: int = MIN_OBSERVATIONS) -> dict[str, float]:
        """Codes dont les retours humains disent qu'ils crient trop.

        Retourne {code: taux de faux positifs} pour les codes dépassant le
        seuil. Le rapport s'en sert pour rétrograder la gravité au lieu de
        supprimer l'alerte : un faux positif fréquent reste parfois vrai.
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT code,
                          SUM(judgement = 'faux_positif') AS fp,
                          COUNT(*) AS total
                   FROM alert_feedback GROUP BY code"""
            ).fetchall()
        muted = {}
        for row in rows:
            if row["total"] >= min_observations:
                rate = row["fp"] / row["total"]
                if rate >= FALSE_POSITIVE_MUTE_RATE:
                    muted[row["code"]] = round(rate, 3)
        return muted

    def agreement(self) -> dict:
        """Taux d'accord entre verdict machine et verdict humain."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT verdict_machine, verdict_human FROM reviews WHERE verdict_machine IS NOT NULL"
            ).fetchall()
        if not rows:
            return {"revues": 0, "accord": None, "detail": {}}
        detail: dict[str, int] = {}
        agreed = 0
        for row in rows:
            key = f"{row['verdict_machine']}→{row['verdict_human']}"
            detail[key] = detail.get(key, 0) + 1
            if row["verdict_machine"] == row["verdict_human"]:
                agreed += 1
        return {"revues": len(rows), "accord": round(agreed / len(rows), 3), "detail": detail}

    def stats(self) -> dict:
        with closing(self._connect()) as conn:
            counts = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("reviews", "corrections", "alert_feedback")
            }
        return {**counts, "accord": self.agreement(), "alertes_calmees": self.muted_alert_codes()}

    # --- réinjection dans le prompt ----------------------------------------

    def prompt_supplement(self, kind: str | None = None, max_fields: int = 5) -> str:
        """Consignes dérivées des corrections humaines, à ajouter au prompt.

        C'est la boucle d'amélioration : chaque correction d'opérateur devient
        une consigne ciblée pour les extractions suivantes. On reste sous un
        nombre réduit de champs — un prompt qui énumère vingt consignes les
        dilue toutes.
        """
        stats = [s for s in self.field_stats(kind=kind, limit=max_fields) if s.corrections >= 2]
        if not stats:
            return ""

        lines = [
            "Retours d'opérateurs humains sur des extractions précédentes du même type "
            "d'annonce. Ces champs ont été corrigés à la main : redouble d'attention.",
        ]
        for stat in stats:
            lines.append(f"- « {stat.field} » : {stat.corrections} correction(s) enregistrée(s).")
            for machine, human in stat.examples:
                if machine and human and machine != human:
                    lines.append(
                        f"    extrait à tort « {machine[:90]} » alors que la valeur correcte "
                        f"était « {human[:90]} »"
                    )
                elif human:
                    lines.append(f"    valeur correcte attendue de la forme « {human[:90]} »")
        return "\n".join(lines)
