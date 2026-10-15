"""Le juge : noter en pourcentage la fiabilité d'une extraction.

Le principe éprouvé ici : la confiance que le modèle s'attribue ne doit jamais
suffire à faire passer un champ pour fiable.
"""

from datetime import date

import pytest

from scrapperbf.core.priority import profile_for
from scrapperbf.core.titles import TitleKind
from scrapperbf.qa.alerts import Code
from scrapperbf.qa.judge import (
    SEUIL_FIABILITE_GLOBALE,
    check_anchoring,
    evaluate_criteria,
    judge,
    judge_field,
)

PROFIL = profile_for(TitleKind.DEMANDE_PRIX)
SOURCE = (
    "Avis de demande de prix n° : 2026-23/MSECU/SG/ONASER/DMP. "
    "Financement : Budget ONASER, exercice 2026. "
    "Montant prévisionnel : vingt cinq millions (25 000 000) FCFA TTC. "
    "Les offres devront parvenir avant le 15/10/2026 09h 00 mn GMT. "
    "L'ouverture des plis sera faite immédiatement. "
    "Téléphone : (00226) 25 37 44 78."
)
PARUTION = date(2026, 10, 6)


def env(valeur, citation="", confiance=0.9):
    return {"valeur": valeur, "citation": citation, "confiance": confiance}


EXTRACTION_HONNETE = {
    "numero_reference": env("2026-23/MSECU/SG/ONASER/DMP", "n° : 2026-23/MSECU/SG/ONASER/DMP", 0.95),
    "date_limite_depot": env("15/10/2026 09h 00 mn GMT",
                             "devront parvenir avant le 15/10/2026 09h 00 mn GMT"),
    "date_ouverture_plis": env("immédiatement", "L'ouverture des plis sera faite immédiatement", 0.8),
    "montant_previsionnel_global": env({"ttc": "25 000 000"},
                                       "vingt cinq millions (25 000 000) FCFA TTC"),
    "financement": env("Budget ONASER, exercice 2026", "Financement : Budget ONASER, exercice 2026"),
}


# --- ancrage ----------------------------------------------------------------

def test_une_citation_presente_est_ancree():
    ancree, recouvrement, _ = check_anchoring("avant le 15/10/2026", SOURCE)
    assert ancree is True and recouvrement > 0.9


def test_une_citation_absente_n_est_pas_ancree():
    ancree, _, motif = check_anchoring("au plus tard le 01/01/2020 à midi pile", SOURCE)
    assert ancree is False and "n'a pas recopié" in motif


def test_une_citation_est_comparee_par_tokens_pas_au_caractere():
    # Les modèles normalisent espaces, apostrophes et casse en recopiant :
    # exiger le littéral exact rejetterait des citations honnêtes.
    assert check_anchoring("L'OUVERTURE  DES   PLIS sera faite immédiatement", SOURCE)[0] is True


def test_sans_citation_l_ancrage_est_indetermine():
    assert check_anchoring("", SOURCE)[0] is None


# --- critères du référentiel ------------------------------------------------

def test_une_date_anterieure_au_bulletin_echoue():
    _, echoues = evaluate_criteria("date_limite_depot", "01/01/2020", SOURCE, {},
                                   bulletin_date=PARUTION)
    assert any("parution" in c for c in echoues)


def test_l_ordre_depot_ouverture_est_controle():
    _, echoues = evaluate_criteria(
        "date_limite_depot", "20/10/2026", SOURCE,
        {"date_ouverture_plis": "15/10/2026"}, bulletin_date=PARUTION,
    )
    assert any("ouverture" in c for c in echoues)


def test_une_ouverture_immediate_n_est_pas_un_echec_d_ordre():
    # « immédiatement » n'est pas une date : il n'y a rien à ordonner.
    _, echoues = evaluate_criteria(
        "date_limite_depot", "15/10/2026", SOURCE,
        {"date_ouverture_plis": "immédiatement"}, bulletin_date=PARUTION,
    )
    assert not any("ouverture" in c for c in echoues)


def test_le_recoupement_lettres_chiffres_est_un_critere():
    reussis, _ = evaluate_criteria("montant_previsionnel_global",
                                   "vingt cinq millions (25 000 000) FCFA", SOURCE, {})
    assert any("lettres" in c for c in reussis)

    _, echoues = evaluate_criteria("montant_previsionnel_global",
                                   "vingt cinq millions (9 999) FCFA", SOURCE, {})
    assert any("lettres" in c for c in echoues)


def test_un_telephone_mal_forme_echoue():
    _, echoues = evaluate_criteria("renseignements_et_contact",
                                   {"telephones": ["123"]}, SOURCE, {})
    assert any("huit chiffres" in c for c in echoues)


def test_des_lots_non_consecutifs_echouent():
    _, echoues = evaluate_criteria("lots", [{"numero": 1}, {"numero": 5}], SOURCE, {})
    # Les critères sont rendus tels qu'écrits dans le référentiel, accentués.
    assert any("consécutifs" in c for c in echoues)


# --- note de champ ----------------------------------------------------------

def test_un_champ_honnete_est_tres_fiable():
    v = judge_field("date_limite_depot", "15/10/2026 09h 00 mn GMT",
                    citation="devront parvenir avant le 15/10/2026 09h 00 mn GMT",
                    model_confidence=0.9, source=SOURCE, bulletin_date=PARUTION)
    assert v.reliable and v.percent >= 90


def test_la_confiance_du_modele_ne_sauve_pas_une_valeur_inventee():
    # Un modèle est aussi sûr de lui quand il se trompe : sa confiance ne pèse
    # qu'un dixième de la note.
    v = judge_field("date_limite_depot", "01/01/2020",
                    citation="au plus tard le 01/01/2020", model_confidence=0.99,
                    source=SOURCE, bulletin_date=PARUTION)
    assert not v.reliable and v.anchored is False and v.percent < 50


def test_un_critere_echoue_plafonne_la_note():
    v = judge_field("montant_previsionnel_global", {"ttc": "25 000 000"},
                    citation="vingt cinq millions (9 999) FCFA TTC",
                    model_confidence=1.0, source=SOURCE)
    assert v.reliability <= 0.55


def test_un_champ_absent_releve_de_la_couverture_non_de_la_fiabilite():
    v = judge_field("financement", None, source=SOURCE)
    assert v.reliability == 0.0 and v.form_status == "missing"
    assert "couverture" in v.reasons[0]


def test_sans_citation_la_note_se_rabat_sur_la_valeur():
    presente = judge_field("financement", "Budget ONASER, exercice 2026", source=SOURCE)
    absente = judge_field("financement", "Budget imaginaire du Luxembourg", source=SOURCE)
    assert presente.reliability > absente.reliability
    assert presente.anchored is None and absente.anchored is None


# --- note globale -----------------------------------------------------------

def test_une_extraction_honnete_passe_le_seuil():
    j = judge("u1", EXTRACTION_HONNETE, PROFIL, source=SOURCE, pages=[21], bulletin_date=PARUTION)
    assert j.reliable and j.score >= SEUIL_FIABILITE_GLOBALE
    assert j.unreliable_fields == []


def test_deux_champs_inventes_font_tomber_le_score():
    falsifiee = dict(EXTRACTION_HONNETE)
    falsifiee["date_limite_depot"] = env("01/01/2020", "au plus tard le 01/01/2020", 0.99)
    falsifiee["montant_previsionnel_global"] = env(
        {"ttc": "99 999 999"}, "Montant prévisionnel : (99 999 999) FCFA", 0.97
    )
    j = judge("u1", falsifiee, PROFIL, source=SOURCE, pages=[21], bulletin_date=PARUTION)
    assert not j.reliable
    assert set(j.unreliable_fields) == {"date_limite_depot", "montant_previsionnel_global"}


def test_les_champs_absents_ne_pesent_pas_sur_la_fiabilite():
    # « Est-ce complet ? » et « est-ce juste ? » sont deux questions, et elles
    # n'ont pas le même remède.
    partielle = {"financement": EXTRACTION_HONNETE["financement"], "objet": env(None)}
    j = judge("u1", partielle, PROFIL, source=SOURCE)
    assert j.reliable


def test_une_valeur_nue_est_jugeable():
    # Pour pouvoir juger une extraction par motifs, qui n'a pas d'enveloppe.
    j = judge("u1", {"financement": "Budget ONASER, exercice 2026"}, PROFIL, source=SOURCE)
    assert j.fields["financement"].reliability > 0.6


def test_le_jugement_alerte_et_renvoie_vers_le_dashboard():
    falsifiee = dict(EXTRACTION_HONNETE)
    falsifiee["date_limite_depot"] = env("01/01/2020", "au plus tard le 01/01/2020", 0.99)
    falsifiee["montant_previsionnel_global"] = env(
        {"ttc": "99 999 999"}, "Montant prévisionnel : (99 999 999) FCFA", 0.97
    )
    j = judge("u1", falsifiee, PROFIL, source=SOURCE, pages=[21], bulletin_date=PARUTION)
    codes = [a.code for a in j.alerts]
    assert Code.CHAMP_NON_ANCRE in codes
    globale = next(a for a in j.alerts if a.code == Code.EXTRACTION_ECHOUEE)
    assert "%" in globale.message and "dashboard" in globale.hint


def test_le_jugement_est_serialisable():
    j = judge("u1", EXTRACTION_HONNETE, PROFIL, source=SOURCE, bulletin_date=PARUTION)
    d = j.to_dict()
    assert 0 <= d["percent"] <= 100 and d["reliable"] is True
    assert all("percent" in v for v in d["fields"].values())
