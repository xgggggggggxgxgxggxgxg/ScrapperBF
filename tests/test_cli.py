import json

import pytest

from scrapperbf.cli import main


@pytest.fixture
def bulletin_file(tmp_path, bulletin):
    path = tmp_path / "quotidien_4466.md"
    path.write_text(bulletin, encoding="utf-8")
    return path


def test_audit_affiche_un_resume(bulletin_file, tmp_path, capsys):
    code = main(["--feedback-db", str(tmp_path / "f.db"), "audit", str(bulletin_file), "--numero", "N4466"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Rapport QA — N4466" in out and "annonces sur 6 pages" in out


def test_audit_ecrit_rapport_et_dashboard(bulletin_file, tmp_path):
    rapport, dash = tmp_path / "out" / "qa.json", tmp_path / "out" / "qa.html"
    main(["--feedback-db", str(tmp_path / "f.db"), "audit", str(bulletin_file),
          "--json", str(rapport), "--dashboard", str(dash)])
    data = json.loads(rapport.read_text(encoding="utf-8"))
    assert data["stats"]["annonces"] >= 4
    assert dash.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_audit_apparie_les_extractions_du_pipeline(bulletin_file, tmp_path, extraction_demande_prix, capsys):
    # Format historique de pipeline.py : liste par section, sans identifiants.
    extractions = tmp_path / "N4466.json"
    extractions.write_text(json.dumps({"AVIS": [extraction_demande_prix]}), encoding="utf-8")
    main(["--feedback-db", str(tmp_path / "f.db"), "audit", str(bulletin_file),
          "--extractions", str(extractions)])
    err = capsys.readouterr().err
    # L'écart de cardinalité est dit, pas masqué.
    assert "appariement positionnel" in err


def test_audit_accepte_les_extractions_indexees(bulletin_file, tmp_path, extraction_demande_prix):
    extractions = tmp_path / "ex.json"
    extractions.write_text(json.dumps({"AVIS#1": extraction_demande_prix}), encoding="utf-8")
    rapport = tmp_path / "qa.json"
    main(["--feedback-db", str(tmp_path / "f.db"), "audit", str(bulletin_file),
          "--extractions", str(extractions), "--json", str(rapport)])
    data = json.loads(rapport.read_text(encoding="utf-8"))
    avis = next(n for n in data["notices"] if n["id"] == "AVIS#1")
    assert avis["score"]["verdict"] == "VALIDE"


def test_code_de_sortie_non_nul_sur_alerte_critique(tmp_path, bulletin_doublon):
    path = tmp_path / "b.md"
    path.write_text(bulletin_doublon, encoding="utf-8")
    code = main(["--feedback-db", str(tmp_path / "f.db"), "audit", str(path), "--fail-on-critical"])
    assert code == 2


def test_titles_liste_le_classement(bulletin_file, tmp_path, capsys):
    main(["--feedback-db", str(tmp_path / "f.db"), "titles", str(bulletin_file), "--verbose"])
    out = capsys.readouterr().out
    assert "DEMANDE_PRIX" in out and "p.  12" in out


def test_fields_affiche_les_priorites(tmp_path, capsys):
    main(["--feedback-db", str(tmp_path / "f.db"), "fields", "DEMANDE_PRIX"])
    out = capsys.readouterr().out
    assert "critique  date_limite_depot" in out


def test_feedback_import_puis_stats(tmp_path, capsys):
    db = tmp_path / "f.db"
    revue = tmp_path / "revue.json"
    revue.write_text(json.dumps({"document": "N4466", "reviews": [
        {"unit_id": "AVIS#1", "kind": "DEMANDE_PRIX", "verdict": "VALIDE",
         "verdict_machine": "A_VERIFIER", "corrections": {"objet": "Acquisition de materiel"},
         "alerts": [{"code": "CHAMP_SUSPECT", "judgement": "faux_positif"}]}
    ]}), encoding="utf-8")
    main(["--feedback-db", str(db), "feedback", "import", str(revue)])
    main(["--feedback-db", str(db), "feedback", "stats"])
    out = capsys.readouterr().out
    assert "1 revue(s)" in out and '"reviews": 1' in out
