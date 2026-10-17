"""Tests hors-ligne du CLI de revue humaine (dossiers + règles redirigés en tmp)."""
import json
import revue_cli
import revue_humaine
import regles

DECISION = {
    "couleur": "rouge", "raison": "champ critique absent",
    "pourquoi_divergence_juges": None, "score": 55,
    "verdict_scoreur": "mauvais", "verdict_llm": "doute",
    "champs_en_conflit": [{"champ": "montant_previsionnel_global", "critique": True,
                           "cause": "absent", "gravite": 3}],
    "divergences": [], "analyse_llm": "montant introuvable",
    "extraction_a": {"objet": "X"}, "extraction_b": {"objet": "X"},
    "champs_finaux": {"objet": "X", "montant_previsionnel_global": None},
}


def _redirect(tmp_path, monkeypatch):
    monkeypatch.setattr(revue_humaine, "REVUE_DIR", tmp_path / "revue")
    monkeypatch.setattr(regles, "REGLES_PATH", tmp_path / "regles.json")


def test_list_and_show(tmp_path, monkeypatch, capsys):
    _redirect(tmp_path, monkeypatch)
    revue_humaine.creer_dossier(DECISION, "texte source lisible", "N4503", 2)
    revue_cli.main(["list"])
    out = capsys.readouterr().out
    assert "N4503-AVIS-2" in out and "dossier" in out.lower()

    revue_cli.main(["show", "N4503-AVIS-2"])
    out = capsys.readouterr().out
    assert "montant_previsionnel_global" in out
    assert "texte source lisible" in out


def test_corriger_creates_rule(tmp_path, monkeypatch, capsys):
    _redirect(tmp_path, monkeypatch)
    revue_humaine.creer_dossier(DECISION, "texte", "N4503", 2)
    revue_cli.main(["corriger", "N4503-AVIS-2", "montant_previsionnel_global",
                    "--valeur", '{"ttc": "80 000 000"}',
                    "--quoi", "recopier le TTC", "--declencheurs", "htva,ttc"])
    out = capsys.readouterr().out
    assert "corrigé" in out

    rules = regles.load_rules(tmp_path / "regles.json")
    assert len(rules) == 1
    assert rules[0]["declencheurs"] == ["htva", "ttc"]
    assert rules[0]["exemple_valeur"] == {"ttc": "80 000 000"}

    # Le dossier est bien passé à "corrige" avec la valeur retenue
    dos = json.loads((tmp_path / "revue" / "N4503-AVIS-2.json").read_text(encoding="utf-8"))
    assert dos["statut"] == "corrige"
    assert dos["champs_proposes"]["montant_previsionnel_global"] == {"ttc": "80 000 000"}


def test_valeur_json_vs_texte():
    assert revue_cli._valeur('{"ttc": "80"}') == {"ttc": "80"}
    assert revue_cli._valeur("texte simple") == "texte simple"


def test_regles_listing(tmp_path, monkeypatch, capsys):
    _redirect(tmp_path, monkeypatch)
    regles.add_rule("AVIS", "objet", "situation", "faire X", path=tmp_path / "regles.json")
    revue_cli.main(["regles"])
    out = capsys.readouterr().out
    assert "faire X" in out
