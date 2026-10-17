"""Tests hors-ligne du moteur de règles (stockage + matching + injection prompt)."""
import regles


def test_add_and_match_rule(tmp_path):
    p = tmp_path / "regles.json"
    regles.add_rule("AVIS", "montant_previsionnel_global",
                    "montant HTVA et TTC collés", "séparer HTVA et TTC",
                    declencheurs=["htva", "ttc"], exemple_valeur={"htva": "80", "ttc": "94"},
                    source="test", path=p)
    rules = regles.load_rules(p)
    assert len(rules) == 1 and rules[0]["id"] == "r1"

    # Déclencheurs présents -> règle applicable
    m = regles.match_rules("Montant HTVA 80 et TTC 94", "AVIS", rules)
    assert len(m) == 1
    # Déclencheur absent -> non applicable
    assert regles.match_rules("Aucun montant ici", "AVIS", rules) == []
    # Mauvaise section -> non applicable
    assert regles.match_rules("htva ttc", "RESULTATS", rules) == []


def test_global_rule_without_triggers(tmp_path):
    p = tmp_path / "regles.json"
    regles.add_rule("AVIS", "objet", "toujours vérifier l'objet",
                    "recopier l'objet complet", declencheurs=[], path=p)
    m = regles.match_rules("n'importe quel texte", "AVIS", regles.load_rules(p))
    assert len(m) == 1  # all([]) == True -> règle globale


def test_dedup(tmp_path):
    p = tmp_path / "regles.json"
    regles.add_rule("AVIS", "objet", "situation X", "faire Y", path=p)
    regles.add_rule("AVIS", "objet", "situation X", "faire Y", path=p)
    assert len(regles.load_rules(p)) == 1  # pas de doublon


def test_load_rules_robust_to_bad_shapes(tmp_path):
    import json as _json
    # Objet {"regles": <objet>} édité à la main -> ne doit PAS planter, renvoie []
    p = tmp_path / "regles.json"
    p.write_text(_json.dumps({"regles": {"r1": {"section": "AVIS"}}}), encoding="utf-8")
    assert regles.load_rules(p) == []
    # {"regles": null} et scalaire -> []
    p.write_text(_json.dumps({"regles": None}), encoding="utf-8")
    assert regles.load_rules(p) == []
    p.write_text("7", encoding="utf-8")
    assert regles.load_rules(p) == []
    # Le wrapper valide {"regles": [...]} fonctionne toujours
    p.write_text(_json.dumps({"regles": [{"section": "AVIS", "champ": "objet"}]}), encoding="utf-8")
    assert len(regles.load_rules(p)) == 1
    # Et le consommateur n'explose pas sur une forme bancale
    p.write_text(_json.dumps({"regles": 7}), encoding="utf-8")
    assert regles.match_rules("texte", "AVIS", regles.load_rules(p)) == []


def test_format_for_prompt(tmp_path):
    p = tmp_path / "regles.json"
    regles.add_rule("AVIS", "objet", "situation X", "faire Y",
                    declencheurs=[], exemple_valeur="valeur", path=p)
    txt = regles.format_for_prompt(regles.load_rules(p))
    assert "RÈGLES APPRISES" in txt and "faire Y" in txt
    assert regles.format_for_prompt([]) == ""
