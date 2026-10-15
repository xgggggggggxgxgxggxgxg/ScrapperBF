"""Nombres français écrits en lettres, et recoupement avec les chiffres.

Le bulletin écrit systématiquement ses montants deux fois :

    « vingt cinq millions (25 000 000) FCFA »

C'est une redondance offerte par le document, et le seul moyen *interne* de
vérifier un montant sans ouvrir le PDF : si les lettres et les chiffres ne
concordent pas, l'un des deux a été abîmé par la reconnaissance de texte. On
s'en sert donc comme contrôle, pas comme simple décoration.
"""

from __future__ import annotations

import re

from ..core.text import normalize

UNITS = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "vingts": 20, "trente": 30, "quarante": 40, "cinquante": 50,
    "soixante": 60, "septante": 70, "octante": 80, "huitante": 80, "nonante": 90,
    "cent": 100, "cents": 100,
}
SCALES = {"mille": 1_000, "milles": 1_000, "million": 1_000_000, "millions": 1_000_000,
          "milliard": 1_000_000_000, "milliards": 1_000_000_000}
IGNORED = {"et", "de", "d", "la", "le", "les", "f", "fcfa", "cfa", "franc", "francs",
           "htva", "ttc", "hd", "ht", "tva", "soit", "environ"}

# « quatre-vingt » et « quatre-vingt-dix » ne se déduisent pas de leurs mots.
COMPOSED = [
    ("quatre vingt dix", 90), ("quatre vingts dix", 90),
    ("quatre vingt", 80), ("quatre vingts", 80),
    ("soixante dix", 70),
]


def parse_french_number(text: str) -> int | None:
    """Convertit un nombre écrit en lettres françaises en entier.

    Retourne ``None`` si aucun mot-nombre exploitable n'est présent — un texte
    qui n'annonce pas de nombre ne doit pas valoir zéro.
    """
    words = normalize(text).replace("-", " ")
    for forme, valeur in COMPOSED:
        words = words.replace(forme, f" §{valeur} ")
    tokens = [t for t in re.split(r"[^\w§]+", words) if t and t not in IGNORED]

    # On retient la DERNIÈRE suite ininterrompue de mots-nombres. Les montants
    # sont collés à leur parenthèse (« …remboursable de vingt cinq mille (25
    # 000) »), et un « un » isolé en début de phrase n'en fait pas partie.
    runs: list[list[str]] = []
    courante: list[str] = []
    for token in tokens:
        if token.startswith("§") or token in UNITS or token in SCALES:
            courante.append(token)
        elif courante:
            runs.append(courante)
            courante = []
    if courante:
        runs.append(courante)
    if not runs:
        return None
    # « un montant de (150 000) FCFA » : ici « un » est l'article, pas un
    # nombre. Une suite réduite au seul mot « un » ne compte donc pas.
    if runs[-1] in (["un"], ["une"]):
        return None

    total, courant = 0, 0
    for token in runs[-1]:
        if token.startswith("§"):
            courant += int(token[1:])
        elif token in UNITS:
            valeur = UNITS[token]
            # « deux cents » multiplie ce qui précède ; « cent » seul vaut 100.
            courant = (courant or 1) * 100 if valeur == 100 else courant + valeur
        else:
            total += (courant or 1) * SCALES[token]
            courant = 0
    return total + courant


DIGITS_IN_PARENS_RE = re.compile(r"\(\s*([\d\s.  ]{2,})\s*\)")


def cross_check_amount(text: str) -> tuple[int | None, int | None, bool | None]:
    """Compare le montant en lettres et le montant entre parenthèses.

    Retourne ``(lettres, chiffres, concordent)``. ``concordent`` vaut ``None``
    quand la comparaison n'est pas possible (une seule des deux écritures).
    """
    m = DIGITS_IN_PARENS_RE.search(text)
    chiffres = int(re.sub(r"\D", "", m.group(1))) if m else None

    avant = text[: m.start()] if m else text
    # On ne lit que la fin du segment : une phrase entière contient d'autres
    # mots-nombres (« trois (03) mois »), le montant est collé à sa parenthèse.
    lettres = parse_french_number(" ".join(normalize(avant).split()[-12:]))

    if lettres is None or chiffres is None:
        return lettres, chiffres, None
    return lettres, chiffres, lettres == chiffres
