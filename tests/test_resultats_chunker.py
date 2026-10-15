"""Tests hors-ligne du découpage intègre des unités RÉSULTATS géantes."""
from resultats_chunker import (
    split_into_lots, chunk_unit, merge_lot_results,
)
from pdf_resultats_extract import find_lot_markers


# --- détection de lots par le texte (plus fiable que la couleur) ------------

def test_find_lot_markers_from_text():
    txt = "Préambule\nLot 1 : acquisition de PC\n...\nLOT N°2 : fournitures\n...\nLot 1 : (répété)"
    lots = find_lot_markers(txt)
    assert len(lots) == 2              # dédupliqué par numéro
    assert lots[0].startswith("Lot 1 :")
    assert lots[1].startswith("Lot 2 :")


def test_find_lot_markers_none():
    assert find_lot_markers("Aucun lot ici, juste un tableau simple") == []


# --- split_into_lots --------------------------------------------------------

def test_split_into_lots_with_markers():
    txt = "REF 123\nColonnes...\nLot 1 : A\nx1 y1\nLot 2 : B\nx2 y2"
    pre, lots = split_into_lots(txt)
    assert "REF 123" in pre
    assert [n for n, _ in lots] == ["1", "2"]
    assert "x1 y1" in lots[0][1] and "x2 y2" in lots[1][1]


def test_split_into_lots_single_unnamed():
    pre, lots = split_into_lots("Tableau sans marqueur de lot")
    assert len(lots) == 1 and lots[0][0] == "1"


# --- chunk_unit : jamais couper au milieu d'un lot --------------------------

def _unit(flat, **kw):
    base = {"emetteur": "MINISTERE X", "macro_section": "MINISTERES",
            "page_debut": 3, "page_fin": 40, "flat_text": flat,
            "_numero": "N4503bis", "_source_pdf": "Quotidien N°4503bis"}
    base.update(kw)
    return base


def test_small_unit_single_chunk():
    chunks = chunk_unit(_unit("Lot 1 : A\nligne"), max_chars=12000)
    assert len(chunks) == 1
    assert chunks[0]["is_continuation"] is False


def test_whole_lots_kept_together_across_chunks():
    lots = "".join(f"Lot {i} : objet {i}\n" + ("row %d\n" % i) * 400 for i in (1, 2, 3))
    flat = "REF\n" + lots
    chunks = chunk_unit(_unit(flat), max_chars=6000)
    assert len(chunks) >= 2
    for ch in chunks:
        assert "ÉMETTEUR (cadre noir) : MINISTERE X" in ch["flat_text"]  # contexte répété
        # un chunk non-continuation contient des lots entiers (début « Lot N : »)
        if not ch["is_continuation"]:
            assert "Lot" in ch["flat_text"]
    # tous les lots sont couverts
    joined = " ".join(c["flat_text"] for c in chunks)
    for i in (1, 2, 3):
        assert f"Lot {i} : objet {i}" in joined


def test_giant_single_lot_is_split_with_continuation():
    big = "Lot 1 : énorme\n" + "".join(f"SOUMISSIONNAIRE_{i} 1000\n" for i in range(2000))
    chunks = chunk_unit(_unit(big), max_chars=5000, overlap_lines=3)
    assert len(chunks) >= 2
    assert any(c["is_continuation"] for c in chunks)
    # chevauchement : une même ligne apparaît à la jointure de deux morceaux
    cont = next(c for c in chunks if c["is_continuation"])
    assert "SUITE du Lot 1" in cont["flat_text"]


# --- merge_lot_results : fusion dédupliquée (intégrité) ---------------------

def test_merge_dedups_overlapping_bidders():
    r1 = {"autorite_contractante": "MINISTERE X", "lots": [
        {"numero": 1, "intitule": "A", "soumissionnaires": [{"nom": "ADS SARL"}, {"nom": "IPCOM"}]}]}
    r2 = {"lots": [  # chevauchement : IPCOM réapparaît, + un nouveau
        {"numero": 1, "intitule": "A", "soumissionnaires": [{"nom": "IPCOM"}, {"nom": "GREEN ENG"}],
         "attributaire_provisoire": {"nom": "ADS SARL"}}]}
    merged = merge_lot_results([r1, r2])
    assert merged["autorite_contractante"] == "MINISTERE X"
    assert merged["nombre_lots"] == 1
    noms = [s["nom"] for s in merged["lots"][0]["soumissionnaires"]]
    assert noms == ["ADS SARL", "IPCOM", "GREEN ENG"]   # IPCOM non dupliqué
    assert merged["lots"][0]["attributaire_provisoire"]["nom"] == "ADS SARL"


def test_merge_distinct_lots_sorted():
    r1 = {"lots": [{"numero": 2, "soumissionnaires": []}]}
    r2 = {"lots": [{"numero": 1, "soumissionnaires": []}]}
    merged = merge_lot_results([r1, r2])
    assert [l["numero"] for l in merged["lots"]] == [1, 2]


def test_merge_infructueux_propagates():
    r = {"lots": [{"numero": 1, "soumissionnaires": [], "infructueux": True}]}
    merged = merge_lot_results([r])
    assert merged["lots"][0]["infructueux"] is True
