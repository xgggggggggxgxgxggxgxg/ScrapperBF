from scrapperbf.core.pages import parse_pages
from scrapperbf.core.segment import segment
from scrapperbf.core.titles import TitleKind


def units(bulletin):
    return segment(parse_pages(bulletin))


def test_le_sommaire_est_exclu_du_decoupage(bulletin):
    assert all(1 not in u.pages for u in units(bulletin))


def test_chaque_annonce_est_isolee(bulletin):
    notices = units(bulletin)
    kinds = [u.kind for u in notices]
    assert TitleKind.DEMANDE_PRIX in kinds
    assert TitleKind.RESULTATS in kinds
    # Les travaux de la page 13 sont bien une annonce distincte de l'avis p. 12.
    assert len(notices) >= 4


def test_les_pages_reelles_sont_portees_par_l_annonce(bulletin):
    avis = next(u for u in units(bulletin) if u.kind is TitleKind.DEMANDE_PRIX)
    assert avis.page_debut == 12


def test_une_annonce_multipage_couvre_ses_deux_pages(bulletin):
    travaux = next(u for u in units(bulletin) if u.marche_type == "Travaux")
    # L'avis de travaux déborde de la page 13 sur la page 14.
    assert travaux.page_debut == 13 and travaux.page_fin == 14


def test_la_categorie_suit_le_marqueur_du_document(bulletin):
    notices = units(bulletin)
    avis = next(u for u in notices if u.kind is TitleKind.DEMANDE_PRIX)
    resultats = next(u for u in notices if u.kind is TitleKind.RESULTATS)
    assert avis.category == "MINISTERES"
    assert resultats.category == "COLLECTIVITES"


def test_le_type_de_marche_ne_survit_pas_au_changement_de_categorie(bulletin):
    resultats = next(u for u in units(bulletin) if u.kind is TitleKind.RESULTATS)
    assert resultats.marche_type is None


def test_l_autorite_est_heritee_ou_presente(bulletin):
    avis = next(u for u in units(bulletin) if u.kind is TitleKind.DEMANDE_PRIX)
    textes = avis.flat_text
    assert "MINISTERE DE L'ECONOMIE" in textes


def test_la_ligne_attributaire_clot_une_unite_de_resultats(bulletin):
    resultats = next(u for u in units(bulletin) if u.kind is TitleKind.RESULTATS)
    assert "ATTRIBUTAIRE" in resultats.flat_text
    # Le communiqué qui suit n'a pas été absorbé dans l'unité de résultats.
    assert "communique" not in resultats.flat_text.lower()


def test_les_fragments_de_titre_sont_recolles(bulletin):
    # Aucun fragment maigre ne doit survivre comme annonce autonome.
    assert all(u.content_chars >= 60 for u in units(bulletin))


def test_la_decision_de_decoupage_est_tracee(bulletin):
    assert all(u.split_reason for u in units(bulletin))


def test_le_texte_aplati_contient_les_tableaux(bulletin):
    resultats = next(u for u in units(bulletin) if u.kind is TitleKind.RESULTATS)
    assert "[TABLEAU]" in resultats.flat_text
    assert "ENTREPRISE ALPHA" in resultats.flat_text


def test_les_identifiants_sont_uniques_et_stables(bulletin):
    notices = units(bulletin)
    ids = [u.id for u in notices]
    assert len(ids) == len(set(ids))
    assert ids == [u.id for u in units(bulletin)]
