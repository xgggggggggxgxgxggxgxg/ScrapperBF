from scrapperbf.core.text import jaccard, normalize, preview, shingles, strip_markup, tokens


def test_strip_markup_retire_emphase_et_placeholders():
    assert strip_markup("**ACQUISITION** de <mark>materiel</mark> <!-- image -->") == "ACQUISITION de materiel"


def test_normalize_plie_accents_et_casse():
    assert normalize("Délai d'Exécution") == "delai d'execution"


def test_tokens_ignore_la_ponctuation():
    assert tokens("N°2026-012/MEF, SG") == ["n", "2026", "012", "mef", "sg"]


def test_textes_courts_identiques_restent_comparables():
    # Sans le cas particulier des textes courts, deux shingles vides
    # donneraient une similarité de 0 pour deux textes pourtant identiques.
    assert jaccard(shingles("entreprise alpha"), shingles("entreprise alpha")) == 1.0


def test_jaccard_distingue_deux_textes_differents():
    a, b = shingles("acquisition de materiel informatique pour la direction"), shingles(
        "travaux de construction d un batiment administratif neuf"
    )
    assert jaccard(a, b) < 0.1


def test_preview_tronque_sans_couper_les_sauts_de_ligne():
    out = preview("ligne un\nligne deux " + "x" * 300, width=40)
    assert len(out) == 40 and "\n" not in out and out.endswith("…")
