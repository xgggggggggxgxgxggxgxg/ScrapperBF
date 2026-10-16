"""Tests hors-ligne du repli LightOCR (backend OCR injecté, pas de vrai PDF)."""
from ocr_fallback import needs_ocr, recover_block, ocr_page, _page0

# Texte charabia (mots longs sans voyelle) = police PDF cassée.
GARBLED = "Lrtprt mntn dcmnt prtcpr Lt N Grpmnt bcdfg hjklmn pqrstv wxz bcdfgh"
CLEAN = ("Avis de demande de prix pour l'acquisition de matériels informatiques "
         "au profit du ministère ; financement budget de l'État.")


def test_needs_ocr_detects_garbled_only():
    assert needs_ocr(GARBLED) is True
    assert needs_ocr(CLEAN) is False


def test_recover_block_noop_on_clean_text():
    blk = {"flat_text": CLEAN, "_page_debut": 3}
    out = recover_block(blk, "x.pdf", ocr_fn=lambda p, i: "NE DOIT PAS ÊTRE APPELÉ")
    assert out is blk                      # inchangé, même objet
    assert "_ocr_tente" not in out


def test_recover_block_replaces_when_ocr_cleaner():
    blk = {"flat_text": GARBLED, "_page_debut": 5}
    out = recover_block(blk, "x.pdf", ocr_fn=lambda p, i: CLEAN)
    assert out["_ocr_tente"] is True
    assert out["_ocr_applique"] is True
    assert out["_alerte_humaine"] is True   # revue humaine systématique
    assert out["flat_text"] == CLEAN
    assert out is not blk                   # copie, l'original n'est pas muté
    assert blk["flat_text"] == GARBLED


def test_recover_block_keeps_text_and_flags_review_when_ocr_fails():
    # pas de backend / OCR vide -> on garde le texte mais on signale la revue
    blk = {"flat_text": GARBLED, "_page_debut": 2}
    out = recover_block(blk, "x.pdf", ocr_fn=lambda p, i: None)
    assert out["_ocr_tente"] is True
    assert out["_ocr_applique"] is False
    assert out["_alerte_humaine"] is True
    assert out["flat_text"] == GARBLED      # pas écrasé par pire


def test_recover_block_rejects_ocr_that_is_not_better():
    # OCR tout aussi corrompu (même charabia) -> on n'applique pas
    blk = {"flat_text": GARBLED, "_page_debut": 1}
    out = recover_block(blk, "x.pdf", ocr_fn=lambda p, i: GARBLED)
    assert out["_ocr_applique"] is False
    assert out["flat_text"] == GARBLED


def test_page0_handles_both_conventions():
    assert _page0({"_page_debut": 7}) == 6
    assert _page0({"page_debut": 4}) == 3
    assert _page0({}) == 0


def test_ocr_page_without_backend_returns_none():
    # provider explicitement None et backend indispo -> None sans exception
    assert ocr_page("x.pdf", 0, provider=None) in (None,)  # inerte hors-ligne
