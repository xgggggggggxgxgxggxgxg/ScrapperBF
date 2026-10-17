"""Tests hors-ligne de la revue humaine (dossiers + correction -> règle)."""
import revue_humaine
import regles

DECISION = {
    "couleur": "rouge",
    "raison": "champ critique absent",
    "pourquoi_divergence_juges": None,
    "score": 55,
    "verdict_scoreur": "mauvais",
    "verdict_llm": "doute",
    "champs_en_conflit": [{"champ": "montant_previsionnel_global", "critique": True,
                           "cause": "absent", "gravite": 3}],
    "divergences": [],
    "analyse_llm": "montant introuvable",
    "extraction_a": {"objet": "X"},
    "extraction_b": {"objet": "X"},
    "champs_finaux": {"objet": "X", "montant_previsionnel_global": None},
}


def test_creer_dossier_contains_everything(tmp_path):
    dos = revue_humaine.creer_dossier(DECISION, "texte source", "N4503", 2, base=tmp_path)
    assert dos["id"] == "N4503-AVIS-2"
    assert dos["couleur"] == "rouge"
    assert dos["texte"] == "texte source"
    assert dos["extraction_a"] == {"objet": "X"}
    assert dos["statut"] == "en_attente"
    assert (tmp_path / "N4503-AVIS-2.json").exists()


def test_correction_creates_rule_and_closes(tmp_path):
    regles_path = tmp_path / "regles.json"
    revue_humaine.creer_dossier(DECISION, "texte", "N4503", 2, base=tmp_path)
    dos = revue_humaine.appliquer_correction(
        "N4503-AVIS-2", "montant_previsionnel_global",
        quoi_faire="recopier le montant TTC écrit en toutes lettres",
        valeur_correcte={"ttc": "80 000 000"},
        declencheurs=["montant", "ttc"], base=tmp_path, regles_path=regles_path)
    assert dos["statut"] == "corrige"
    assert dos["champs_proposes"]["montant_previsionnel_global"] == {"ttc": "80 000 000"}
    # une règle réutilisable a été créée
    rules = regles.load_rules(regles_path)
    assert len(rules) == 1
    assert rules[0]["champ"] == "montant_previsionnel_global"


def test_creer_dossier_idempotent_preserves_correction(tmp_path):
    regles_path = tmp_path / "regles.json"
    revue_humaine.creer_dossier(DECISION, "texte", "N4503", 2, base=tmp_path)
    revue_humaine.appliquer_correction(
        "N4503-AVIS-2", "montant_previsionnel_global",
        quoi_faire="recopier le TTC", valeur_correcte={"ttc": "80 000 000"},
        base=tmp_path, regles_path=regles_path)
    # Re-traitement : creer_dossier rappelé pour le même (numero, index) ne doit PAS
    # écraser la correction humaine.
    again = revue_humaine.creer_dossier(DECISION, "texte", "N4503", 2, base=tmp_path)
    assert again["statut"] == "corrige"
    assert again["corrections"]  # la correction est préservée
    assert again["champs_proposes"]["montant_previsionnel_global"] == {"ttc": "80 000 000"}


def test_lister_filtre_par_statut(tmp_path):
    revue_humaine.creer_dossier(DECISION, "t", "N1", 0, base=tmp_path)
    assert len(revue_humaine.lister("en_attente", base=tmp_path)) == 1
    assert len(revue_humaine.lister("corrige", base=tmp_path)) == 0
