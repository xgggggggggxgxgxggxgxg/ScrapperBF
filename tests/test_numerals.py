"""Nombres en lettres, et recoupement avec les chiffres entre parenthèses."""

import pytest

from scrapperbf.extract.numerals import cross_check_amount, parse_french_number


@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("vingt cinq millions", 25_000_000),
        ("vingt un million cent quatre vingt six mille quatre cent quarante un", 21_186_441),
        ("soixante-quinze mille", 75_000),
        ("quatre-vingt-dix", 90),
        ("quatre-vingts", 80),
        ("soixante-dix", 70),
        ("deux cents", 200),
        ("cent", 100),
        ("cent cinquante mille", 150_000),
        ("deux milliards", 2_000_000_000),
        ("trois", 3),
        ("aucun nombre ici", None),
    ],
)
def test_lecture_des_nombres_en_lettres(texte, attendu):
    assert parse_french_number(texte) == attendu


def test_la_derniere_suite_de_mots_nombres_fait_foi():
    # « un » ouvre la phrase sans faire partie du montant : c'est la suite
    # collée à la parenthèse qui est le montant.
    assert parse_french_number("un montant non remboursable de vingt cinq mille") == 25_000


def test_un_mot_inconnu_separe_deux_nombres():
    assert parse_french_number("trois lots de cinquante") == 50


@pytest.mark.parametrize(
    "texte,lettres,chiffres,concordent",
    [
        ("vingt cinq millions (25 000 000) FCFA TTC", 25_000_000, 25_000_000, True),
        ("soixante-quinze mille (75 000) FCFA", 75_000, 75_000, True),
        ("engagés pendant quatre-vingt-dix (90) jours", 90, 90, True),
        # Discordance : l'une des deux écritures a été abîmée.
        ("vingt cinq millions (9 999) FCFA", 25_000_000, 9_999, False),
        # Comparaison impossible : une seule des deux écritures est là.
        ("un montant de (150 000) FCFA", None, 150_000, None),
        ("vingt cinq millions de francs", 25_000_000, None, None),
    ],
)
def test_recoupement_lettres_chiffres(texte, lettres, chiffres, concordent):
    assert cross_check_amount(texte) == (lettres, chiffres, concordent)
