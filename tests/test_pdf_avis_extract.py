"""Tests hors-ligne des fonctions pures de l'extracteur PDF (sans PDF réel)."""
from pdf_avis_extract import (
    detect_type, trim_reference, is_emetteur, find_depot,
    split_financement, enrich_coverage, parse_montant, CORE_FIELDS,
)


def test_detect_type_recognizes_headers():
    assert detect_type("Avis de demande de prix") == "Avis De Demande De Prix"
    assert detect_type("AVIS D'APPEL D'OFFRES OUVERT").lower().startswith("avis d")
    assert detect_type("Avis à manifestation d'intérêt") is not None
    assert detect_type("Avis de demande de prix à commandes") is not None


def test_detect_type_rejects_non_headers():
    assert detect_type("Le présent marché concerne l'acquisition de biens") is None
    assert detect_type("Financement : Budget de l'État") is None


def test_trim_reference_cuts_trailing_field():
    assert trim_reference("2026-118/MEF/SG/DMP du 28/09/2026 Financement : budget") == "N° 2026-118/MEF/SG/DMP du 28/09/2026"
    assert trim_reference("2026-01/SOPAMIB/DG/PRCP Maître d'ouvrage") == "N° 2026-01/SOPAMIB/DG/PRCP"


def test_trim_reference_empty():
    assert trim_reference("") is None


def test_is_emetteur_detects_uppercase_entities():
    assert is_emetteur("MINISTERE DE L'ECONOMIE ET DES FINANCES") is True
    assert is_emetteur("SOCIETE NATIONALE DE GESTION DU STOCK") is True


def test_is_emetteur_rejects_normal_text():
    assert is_emetteur("Acquisition de fournitures de bureau au profit du service") is False
    assert is_emetteur("abc") is False  # trop court


def test_find_depot_patterns():
    assert "au plus tard le" in find_depot("Les offres devront parvenir au plus tard le 28 octobre 2026 à 09h00.").lower()
    assert find_depot("jusqu'au 15 novembre 2026 à 9 heures") is not None
    assert find_depot("Aucune date ici") is None


def test_find_depot_avant_le():
    # cas réel signalé : « … Tél. : … avant le 19/10/2026 à 09 heure »
    txt = "remises au Secrétariat de la DMP, Tél. : (00226) 25 40 86 52, avant le 19/10/2026 à 09 heure ."
    d = find_depot(txt)
    assert d is not None and "19/10/2026" in d


def test_parse_montant_classifies_htva_ttc():
    raw = ("cinquante millions (50 847 458) francs CFA HTVA soit soixante millions "
           "(60 000 000) francs CFA TTC Cet Avis de demande de prix fait suite")
    m = parse_montant(raw)
    assert "HTVA" in m["htva"] and "50 847 458" in m["htva"]
    assert "TTC" in m["ttc"] and "60 000 000" in m["ttc"]
    assert "Cet" not in (m["htva"] + m["ttc"])  # queue parasite coupée


def test_parse_montant_brut_fallback():
    m = parse_montant("un montant global de 10 000 000 FCFA")
    assert "brut" in m and "10 000 000" in m["brut"]
    assert parse_montant("") is None


def test_split_financement_stops_at_next_field():
    assert split_financement("Budget de l'État, Exercice 2026 Cet avis fait suite") == "Budget de l'État, Exercice 2026"
    assert split_financement("budget communal 2026 Identifiant de l'Autorité") == "budget communal 2026"


def test_enrich_coverage_full():
    a = {k: "x" for k in CORE_FIELDS}
    enrich_coverage(a)
    assert a["_score_exactitude"] == 100
    assert a["_champs_a_verifier"] == []
    assert a["_mode"] == "deterministe"
    assert len(a["_analyse"]) == len(CORE_FIELDS)


def test_enrich_coverage_partial_flags_missing():
    a = {"autorite_emettrice": "X", "objet": "Y", "type_avis": "Z"}  # 3/6
    enrich_coverage(a)
    assert a["_score_exactitude"] == 50
    assert set(a["_champs_a_verifier"]) == {"numero_reference", "financement", "date_limite_depot"}
