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
import logging
from pathlib import Path
from datetime import datetime

from logging_config import setup_logging

log = logging.getLogger("scrapperbf.pipeline")

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
import feedback_store
import config

# Chaîne PDF déterministe (1re passe sans LLM) + routage vers l'API.
from pdf_avis_extract import extract_avis_from_pdf, enrich_coverage, CORE_FIELDS
from pdf_resultats_extract import segment_resultats
from resultats_llm import extract_resultats_unit
from decision_model import refine_if_needed, decide
from format_validator import validate_format
from consensus_extract import consensus_extract
from section_split import split_sections
import judge

DB_PATH = BASE_DIR / "ingestion.db"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# Couche 4 (auto-analyse + auto-complétion) activée par défaut ; désactivable
# (ENABLE_REVIEW=0) pour un run purement déterministe/première passe.
ENABLE_REVIEW = os.environ.get("ENABLE_REVIEW", "1") not in ("0", "false", "False", "")

# On n'appelle l'API que si la révision est activée ET qu'au moins un provider
# est configuré (sinon : chaîne 100 % déterministe, aucune erreur réseau).
def _use_api() -> bool:
    return ENABLE_REVIEW and config.is_configured()


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
    setup_logging()
    conn = init_db()

    if already_processed(conn, numero):
        log.info("[%s] déjà traité avec succès -> ignoré", numero)
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
        # Rétroaction : rappel des erreurs récurrentes des runs précédents,
        # construit AVANT la boucle pour bénéficier à toutes les unités de ce run.
        feedback_hint = feedback_store.build_hint(section) if ENABLE_REVIEW else ""

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
                    reviewed = review_and_complete(
                        flat_text, extracted, section, feedback_hint=feedback_hint
                    )
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

            # Score d'exactitude < 100 % -> on enregistre les raisons pour que
            # les prochains traitements s'améliorent (boucle de rétroaction).
            score = extracted.get("_score_exactitude")
            if score is not None and score < 100:
                feedback_store.record(
                    extracted.get("_raisons_ecart", []), numero, section, score=score
                )

            extracted["_page_debut"] = unit["page_debut"]
            extracted["_page_fin"] = unit["page_fin"]
            # Provenance : de quel bulletin / fichier PDF vient cette annonce.
            extracted["_numero"] = numero
            extracted["_source"] = Path(md_path).name
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

    log.info("[%s] %d unités OK, %d échecs -> %s", numero, nb_ok, nb_echec, out_file)


def _resultat_deterministe(unit: dict) -> dict:
    """Vue déterministe d'une unité RÉSULTATS quand l'API n'est pas appelée :
    émetteur + lots repérés + texte brut (pour affichage), sans structuration fine."""
    return {
        "autorite_contractante": unit.get("emetteur"),
        "macro_section": unit.get("macro_section"),
        "nombre_lots": unit.get("nb_lots_detectes"),
        "lots": [{"intitule": l} for l in (unit.get("lots") or [])],
        "texte_brut": (unit.get("flat_text") or "")[:4000],
        "_mode": "deterministe",
        "page_debut": unit.get("page_debut"), "page_fin": unit.get("page_fin"),
        "_numero": unit.get("_numero"), "_source_pdf": unit.get("_source_pdf"),
        "_source": unit.get("_source"),
    }


def process_pdf(pdf_path: str, numero: str = None):
    """Chaîne complète directement sur un PDF de Quotidien :
      AVIS      : regex (1re passe) -> modèle de décision -> API (si nécessaire)
      RÉSULTATS : segmentation (cadres noirs) -> chunk -> API -> fusion
    Écrit output/<numero>.json = {AVIS, RESULTATS, meta}, trace l'idempotence en
    SQLite. 100 % déterministe si aucun provider n'est configuré."""
    pdf_path = str(pdf_path)
    if numero is None:
        import re as _re
        name = Path(pdf_path).stem.replace("_", " ")
        m = _re.search(r"quotidien\s*n?[°o]?\s*(\d{3,4})\s*(bis)?", name, _re.I)
        numero = ("N" + m.group(1) + ("bis" if m.group(2) else "")) if m else Path(pdf_path).stem

    setup_logging()
    conn = init_db()
    if already_processed(conn, numero):
        log.info("[%s] déjà traité avec succès -> ignoré", numero)
        conn.close()
        return

    conn.execute("INSERT OR REPLACE INTO documents VALUES (?, 'en_cours', ?, 0, 0)",
                 (numero, datetime.now().isoformat()))
    conn.commit()

    use_api = _use_api()
    log.info("[%s] traitement de %s — mode = %s", numero, Path(pdf_path).name,
             f"consensus LLM ({len(config.provider_specs())} provider(s))" if use_api
             else "sans clé LLM (champs non extraits : regex retiré)")

    # ---- Étape 1 : SPLIT du PDF en zones de pages AVIS / RÉSULTATS ------
    zones = split_sections(pdf_path)
    avis_zone, resultats_zone = zones["AVIS"], zones["RESULTATS"]

    # ---- AVIS : garde-fou FORMAT -> extraction (consensus 3 modèles, sinon
    #      regex) -> JUGE. L'extraction par consensus remplace le regex dès qu'une
    #      API est configurée ; sinon le regex sert de repli déterministe.
    avis_out = []
    feedback_hint = feedback_store.build_hint("AVIS") if use_api else ""

    def _finalize_avis(rec, text, fmt_info):
        enrich_coverage(rec)
        if use_api:
            # Extraction par CONSENSUS (2 extracteurs + 1 arbitre).
            ex = consensus_extract(text, "AVIS")
            if ex is not None:
                rec["_consensus"] = ex.pop("_consensus", None)
                for k, v in ex.items():
                    if not k.startswith("_"):
                        rec[k] = v
                enrich_coverage(rec)
            else:  # providers indisponibles -> repli Couche 4 (décision + révision)
                refined, dec = refine_if_needed(rec, text, "AVIS")
                rec = refined if refined else rec
                rec["_decision"] = {"needs_llm": dec["needs_llm"],
                                    "champs_a_reprendre": dec["champs_a_reprendre"]}
            sc = rec.get("_score_exactitude")
            if sc is not None and sc < 100:
                feedback_store.record(rec.get("_raisons_ecart", []), numero, "AVIS", score=sc)
        else:
            rec["_decision"] = {"needs_llm": decide(rec, text)["needs_llm"]}
        rec["_format"] = fmt_info
        rec["_judge"] = judge.judge_avis(rec, text)
        rec.pop("flat_text", None)
        return rec

    nb_bloque = nb_divise = 0
    for unit in extract_avis_from_pdf(pdf_path, numero=numero, page_range=avis_zone):
        fmt = validate_format(unit)
        fmt_info = {k: fmt[k] for k in ("action", "pages", "nb_annonces", "alerte_humaine", "raisons")}

        if fmt["action"] == "bloquer":
            nb_bloque += 1
            log.warning("[%s] bloc BLOQUÉ (p.%s) : %s", numero, fmt["pages"], " ; ".join(fmt["raisons"]))
            unit.pop("flat_text", None)
            unit["_format"] = fmt_info
            unit["_bloque"] = True
            unit["_judge"] = {"verdict": "rejete", "confiance": 0.0, "revue_humaine": True,
                              "checks": [], "anomalies": [{"nom": "format", "statut": "fail", "detail": r}
                                                          for r in fmt["raisons"]]}
            avis_out.append(unit)
            continue

        if use_api and fmt["action"] == "diviser" and fmt.get("sous_annonces"):
            nb_divise += 1
            log.info("[%s] bloc DIVISÉ en %d sous-annonces (p.%s)", numero,
                     len(fmt["sous_annonces"]), fmt["pages"])
            for sub in fmt["sous_annonces"]:
                rec = {k: unit.get(k) for k in ("_numero", "_source_pdf", "_source",
                                                "_page_debut", "_page_fin")}
                rec["flat_text"] = sub
                avis_out.append(_finalize_avis(rec, sub, fmt_info))
        else:
            if fmt["action"] == "diviser":
                nb_divise += 1
                fmt_info = dict(fmt_info, alerte_humaine=True)  # non divisé hors API -> revue humaine
            avis_out.append(_finalize_avis(unit, unit.get("flat_text", ""), fmt_info))

    log.info("[%s] AVIS : %d enregistrement(s) (%d bloqués, %d divisés)",
             numero, len(avis_out), nb_bloque, nb_divise)

    # ---- RÉSULTATS : segmentation -> chunk -> API -> fusion -------------
    resultats_out = []
    r_units = segment_resultats(pdf_path, numero=numero, page_range=resultats_zone)
    log.info("[%s] RÉSULTATS : %d émetteur(s) à structurer", numero, len(r_units))
    for unit in r_units:
        if use_api:
            r = extract_resultats_unit(unit) or _resultat_deterministe(unit)
        else:
            r = _resultat_deterministe(unit)
        r["_judge"] = judge.judge_resultats(r, unit.get("flat_text", "") or r.get("texte_brut", ""))
        resultats_out.append(r)

    # Synthèse qualité pour le meta (file de revue humaine).
    confs = [a["_judge"]["confiance"] for a in avis_out]
    revue = sum(1 for a in avis_out if a["_judge"]["revue_humaine"]) + \
        sum(1 for r in resultats_out if r["_judge"]["revue_humaine"])

    # ---- Stockage -------------------------------------------------------
    payload = {
        "meta": {"numero": numero, "source": Path(pdf_path).name,
                 "date_traitement": datetime.now().isoformat(),
                 "mode": "api" if use_api else "deterministe",
                 "split_pages": {"AVIS": avis_zone, "RESULTATS": resultats_zone,
                                 "n_pages": zones["n_pages"]},
                 "nb_avis": len(avis_out), "nb_resultats_emetteurs": len(resultats_out),
                 "confiance_moyenne_avis": round(sum(confs) / len(confs), 3) if confs else None,
                 "nb_revue_humaine": revue},
        "AVIS": avis_out,
        "RESULTATS": resultats_out,
    }
    out_file = OUTPUT_DIR / f"{numero}.json"
    out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    conn.execute("UPDATE documents SET statut='ok', nb_unites_ok=?, nb_unites_echec=0 WHERE numero=?",
                 (len(avis_out) + len(resultats_out), numero))
    conn.commit()
    conn.close()
    log.info("[%s] terminé : %d avis, %d émetteurs résultats, confiance moy. %s, "
             "%d en revue humaine -> %s", numero, len(avis_out), len(resultats_out),
             payload["meta"]["confiance_moyenne_avis"], revue, out_file)
    return payload


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
    src = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent / "quotidien_4466.md")
    numero = sys.argv[2] if len(sys.argv) > 2 else None
    # .pdf -> chaîne PDF complète (AVIS + RÉSULTATS) ; .md -> ancienne chaîne AVIS.
    if src.lower().endswith(".pdf"):
        process_pdf(src, numero)
    else:
        process_document(src, numero or "N4466")
