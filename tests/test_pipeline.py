"""Tests d'intégration de la couche 4, sans aucun appel réseau.

``extract_unit`` est remplacé par un faux extracteur : on éprouve le pipeline
(idempotence, reprise, persistance du contrôle qualité, injection des retours
humains dans le prompt), pas le modèle.
"""

import json
import sqlite3

import pytest

import pipeline


@pytest.fixture
def env(tmp_path, monkeypatch, bulletin):
    md = tmp_path / "quotidien_4466.md"
    md.write_text(bulletin, encoding="utf-8")
    monkeypatch.setattr(pipeline, "DB_PATH", tmp_path / "ingestion.db")
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path / "output")
    pipeline.OUTPUT_DIR.mkdir(exist_ok=True)
    return md, tmp_path


def faux_extracteur(appels=None, valide=True):
    def extract_unit(flat_text, section, max_retries=3, extra_instructions=""):
        if appels is not None:
            appels.append({"section": section, "instructions": extra_instructions})
        if not valide:
            return None
        if section == "RESULTATS":
            return {
                "autorite_contractante": "Region du Centre-Nord",
                "objet": "Entretien des voiries urbaines de la commune de Kaya",
                "attributaire_provisoire": "ENTREPRISE ALPHA",
                "montant_attribue_min": "18 500 000",
                "nombre_plis_recus": "3",
                "date_deliberation": "02 juillet 2026",
            }
        return {
            "autorite_emettrice": "Ministere de l'Economie",
            "objet": "Acquisition de materiel informatique",
            "numero_reference": "N°2026-012/MEF/SG/DMP",
            "date_limite_depot": "14 aout 2026 a 09 heures 00",
        }
    return extract_unit


def test_validation_minimale_sur_les_champs_requis():
    assert pipeline.is_valid({"autorite_emettrice": "MEF", "objet": "Acquisition"}, "AVIS")
    assert not pipeline.is_valid({"objet": "Acquisition"}, "AVIS")
    assert not pipeline.is_valid({"autorite_emettrice": "MEF", "objet": ""}, "AVIS")


def test_traitement_complet_produit_sorties_et_qualite(env, monkeypatch):
    md, tmp = env
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur())
    stats = pipeline.process_document(str(md), "N4466", dashboard=True, use_feedback=False)

    assert stats["annonces"] >= 4
    data = json.loads((pipeline.OUTPUT_DIR / "N4466.json").read_text(encoding="utf-8"))
    assert data["AVIS"] and data["RESULTATS"]
    assert (pipeline.OUTPUT_DIR / "N4466_qa.json").exists()
    assert (pipeline.OUTPUT_DIR / "N4466_dashboard.html").exists()


def test_les_metadonnees_de_segmentation_sont_ecrites(env, monkeypatch):
    md, _ = env
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur())
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False)
    avis = json.loads((pipeline.OUTPUT_DIR / "N4466.json").read_text(encoding="utf-8"))["AVIS"][0]
    # Ces clés sortaient systématiquement vides avant correction : la
    # segmentation écrit `category`/`marche_type`, pas `avis_category`.
    assert avis["categorie_emettrice"] == "MINISTERES"
    assert avis["_page_debut"] == 12 and avis["_unit_id"].startswith("AVIS#")
    assert avis["_nature"] == "DEMANDE_PRIX"


def test_les_notes_et_alertes_sont_persistees(env, monkeypatch):
    md, tmp = env
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur())
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False)

    conn = sqlite3.connect(pipeline.DB_PATH)
    lignes = conn.execute(
        "SELECT unit_id, kind, page_debut, verdict, confiance FROM qualite_unites WHERE numero='N4466'"
    ).fetchall()
    alertes = conn.execute("SELECT code, pages FROM alertes WHERE numero='N4466'").fetchall()
    conn.close()
    assert lignes and all(0.0 <= l[4] <= 1.0 for l in lignes)
    assert any(l[1] == "DEMANDE_PRIX" and l[2] == 12 for l in lignes)
    assert alertes and all(json.loads(a[1]) for a in alertes if a[1] != "[]")


def test_un_document_reussi_n_est_pas_retraite(env, monkeypatch):
    md, _ = env
    appels = []
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur(appels))
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False)
    premier = len(appels)
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False)
    # Idempotence : le backfill se relance sans repayer les appels d'API.
    assert len(appels) == premier


def test_force_permet_de_retraiter(env, monkeypatch):
    md, _ = env
    appels = []
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur(appels))
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False)
    premier = len(appels)
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False, force=True)
    assert len(appels) == 2 * premier


def test_les_echecs_sont_enregistres_puis_rejouables(env, monkeypatch):
    md, _ = env
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur(valide=False))
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=False)

    conn = sqlite3.connect(pipeline.DB_PATH)
    statut = conn.execute("SELECT statut FROM documents WHERE numero='N4466'").fetchone()[0]
    echecs = conn.execute("SELECT COUNT(*) FROM unites_echouees WHERE numero='N4466'").fetchone()[0]
    conn.close()
    assert statut == "echec_partiel" and echecs > 0

    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur())
    recuperees = pipeline.reprocess_failures("N4466", use_feedback=False)
    assert recuperees > 0

    conn = sqlite3.connect(pipeline.DB_PATH)
    restants = conn.execute("SELECT COUNT(*) FROM unites_echouees WHERE numero='N4466'").fetchone()[0]
    conn.close()
    assert restants < echecs


def test_les_retours_humains_sont_injectes_dans_le_prompt(env, monkeypatch, tmp_path):
    md, _ = env
    from scrapperbf.feedback.store import FeedbackStore

    db = tmp_path / "feedback.db"
    store = FeedbackStore(db)
    for unit in ("AVIS#1", "AVIS#2"):
        store.record_correction("N4400", unit, "date_limite_depot",
                                "14 aout 2026 a 09 heures 00", "14 aout 2026", kind="DEMANDE_PRIX")
    monkeypatch.setattr(pipeline, "FeedbackStore", lambda *a, **k: FeedbackStore(db))

    appels = []
    monkeypatch.setattr(pipeline, "extract_unit", faux_extracteur(appels))
    pipeline.process_document(str(md), "N4466", dashboard=False, use_feedback=True)

    instructions = [a["instructions"] for a in appels if a["instructions"]]
    assert instructions and "date_limite_depot" in instructions[0]
