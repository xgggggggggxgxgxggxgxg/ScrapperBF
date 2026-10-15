"""Test d'orchestration de process_pdf (sans PDF ni API : extracteurs monkeypatchés)."""
import json
import pipeline


def _fake_avis(path, numero=None):
    return [{
        "autorite_emettrice": "MINISTERE X", "objet": "Acquisition de PC",
        "categorie": "Fournitures et Services courants", "type_avis": "Avis De Demande De Prix",
        "numero_reference": "N° 2026-1", "financement": "Budget de l'État 2026",
        "montant_previsionnel_global": {"htva": "50M", "ttc": "60M"},
        "date_limite_depot": "avant le 19/10/2026 à 09 heure",
        "flat_text": "texte de l'annonce", "_numero": numero, "_source_pdf": "Quotidien N°4503",
    }]


def _fake_resultats(path, numero=None):
    return [{
        "emetteur": "MINISTERE Y", "macro_section": "MINISTERES",
        "nb_lots_detectes": 2, "lots": ["Lot 1 : A", "Lot 2 : B"],
        "flat_text": "grand tableau de résultats", "page_debut": 3, "page_fin": 5,
        "_numero": numero, "_source_pdf": "Quotidien N°4503",
    }]


def test_process_pdf_deterministic(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(pipeline.config, "is_configured", lambda: False)  # aucune clé
    monkeypatch.setattr(pipeline, "extract_avis_from_pdf", _fake_avis)
    monkeypatch.setattr(pipeline, "segment_resultats", _fake_resultats)

    payload = pipeline.process_pdf("/x/Quotidien_N_4503.pdf")

    assert payload["meta"]["numero"] == "N4503"          # numéro bien déduit du nom
    assert payload["meta"]["mode"] == "deterministe"
    assert payload["meta"]["nb_avis"] == 1
    assert payload["meta"]["nb_resultats_emetteurs"] == 1

    a = payload["AVIS"][0]
    assert "flat_text" not in a                          # allégé
    assert a["_score_exactitude"] == 100                 # tous les champs clés présents
    assert "_decision" in a

    r = payload["RESULTATS"][0]
    assert r["autorite_contractante"] == "MINISTERE Y"
    assert r["nombre_lots"] == 2
    assert r["_mode"] == "deterministe"

    # fichier écrit et relisible
    out = json.loads((tmp_path / "N4503.json").read_text(encoding="utf-8"))
    assert out["meta"]["source"] == "Quotidien_N_4503.pdf"
    assert len(out["RESULTATS"]) == 1


def test_resultat_deterministe_shape():
    u = {"emetteur": "MIN Z", "macro_section": "REGIONS", "nb_lots_detectes": 3,
         "lots": ["Lot 1 : x"], "flat_text": "t" * 5000, "page_debut": 8, "page_fin": 9,
         "_numero": "N4505", "_source_pdf": "Quotidien N°4505"}
    r = pipeline._resultat_deterministe(u)
    assert r["autorite_contractante"] == "MIN Z"
    assert r["nombre_lots"] == 3
    assert len(r["texte_brut"]) <= 4000                  # tronqué
    assert r["_source_pdf"] == "Quotidien N°4505"
