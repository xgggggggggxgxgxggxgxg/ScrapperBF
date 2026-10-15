"""Contrôles au niveau du champ : validité, plausibilité, ancrage.

Trois questions distinctes sont posées à chaque valeur extraite, et il faut les
garder séparées parce qu'elles n'ont pas les mêmes remèdes :

* **validité de forme** — « 14 aout 2026 » est une date, « voir ci-dessus » non.
  Remède : corriger le prompt d'extraction.
* **plausibilité** — un montant de 12 F CFA ou une date en 1998 passent la
  forme mais sont faux. Remède : relecture humaine de la page.
* **ancrage** (*grounding*) — la valeur apparaît-elle réellement dans le texte
  de la page ? C'est le seul contrôle qui attrape une hallucination du LLM, et
  le plus important pour « est-ce que l'info récupérée est OK ».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from enum import Enum

from ..core.text import normalize

MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
}

NUMBER_RE = re.compile(r"\d[\d\s.,  ]*\d|\d")
CURRENCY_RE = re.compile(r"f\s*cfa|fcfa|\bxof\b|\bfrancs?\b|\beuros?\b|\busd\b|\bf\b")
DATE_TEXT_RE = re.compile(r"(\d{1,2})\s*(?:er)?\s+([a-z]+)\s+(\d{4})")
DATE_NUM_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")
TIME_RE = re.compile(r"(\d{1,2})\s*(?:h|:)\s*(\d{2})?")
# Les références du bulletin s'écrivent « N°2026-012/MEF/SG/DMP », mais la
# valeur extraite d'une étiquette « n° : … » arrive sans son « n° ». Les deux
# formes sont donc acceptées : exiger le préfixe reviendrait à juger suspecte
# une référence que l'on vient soi-même de nettoyer.
REFERENCE_RE = re.compile(r"(?:n\s*[°ºo]\s*)?\d{2,4}\s*[-/]\s*\w")
PHONE_RE = re.compile(r"(?:\+?226)?\s*(\d[\d\s.\-]{6,}\d)")
# Le corpus écrit ses quantités en lettres puis en chiffres entre parenthèses :
# « un délai de soixante (60) jours ». La parenthèse fermante se glisse donc
# entre le nombre et son unité.
DELAI_RE = re.compile(r"(\d+)\s*\)?\s*(jours?|mois|semaines?|ans?|annees?)")
PLACEHOLDER_RE = re.compile(
    r"^(n\s*/?\s*a|na|nc|neant|non\s+(precise|renseigne|mentionne|disponible|specifie)"
    r"|inconnu|voir\s+(ci[- ]dessus|ci[- ]dessous|tableau|plus\s+haut)|idem|id\.?"
    r"|a\s+determiner|\?+|-+|\.+|null|none|aucun)$"
)
# Un bloc OCR dégradé se reconnaît à sa densité de caractères non alphabétiques
# et aux répétitions improbables ("nnnn", "||||").
GARBLE_REPEAT_RE = re.compile(r"(.)\1{4,}")

MIN_PLAUSIBLE_AMOUNT = 1_000           # en dessous : OCR a perdu les milliers
MAX_PLAUSIBLE_AMOUNT = 10_000_000_000_000
MIN_PLAUSIBLE_YEAR = 2005
MAX_PLAUSIBLE_YEAR = 2100


class Status(str, Enum):
    OK = "ok"
    SUSPECT = "suspect"        # formellement acceptable, mais douteux
    INVALID = "invalid"        # ne correspond pas au type attendu
    MISSING = "missing"
    UNGROUNDED = "ungrounded"  # absent du texte source : hallucination probable

    @property
    def is_problem(self) -> bool:
        return self is not Status.OK


@dataclass
class FieldCheck:
    """Verdict porté sur un champ extrait."""

    field: str
    status: Status
    reason: str = ""
    normalized: object | None = None
    hint: str = ""
    evidence: list[str] = dc_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


# --- parsing des valeurs ----------------------------------------------------

def parse_amount(value: str) -> int | None:
    """Montant écrit à la burkinabè (« 1 500 000 F CFA ») → entier.

    Les séparateurs de milliers sont retirés ; une partie décimale à deux
    chiffres est tronquée (les montants publics sont annoncés en francs
    entiers, et « ,00 » est systématique).
    """
    if not value:
        return None
    text = normalize(str(value))
    m = NUMBER_RE.search(text)
    if not m:
        return None
    digits = m.group(0)
    decimal = re.search(r"[.,](\d{1,2})\s*$", digits)
    if decimal:
        digits = digits[: decimal.start()]
    digits = re.sub(r"\D", "", digits)
    return int(digits) if digits else None


def parse_date(value: str) -> tuple[int, int, int] | None:
    """Date française textuelle ou numérique → (année, mois, jour)."""
    if not value:
        return None
    text = normalize(str(value))
    m = DATE_TEXT_RE.search(text)
    if m:
        day, month_name, year = int(m.group(1)), m.group(2), int(m.group(3))
        month = MONTHS.get(month_name)
        if month and 1 <= day <= 31:
            return (year, month, day)
    m = DATE_NUM_RE.search(text)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        if 1 <= month <= 12 and 1 <= day <= 31:
            return (year, month, day)
    return None


def is_placeholder(value: object) -> bool:
    """Détecte les valeurs « remplies » qui ne portent aucune information."""
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    if isinstance(value, bool):
        return False
    text = normalize(str(value))
    return not text or bool(PLACEHOLDER_RE.match(text))


def looks_garbled(value: str) -> bool:
    """Signature d'un texte abîmé par l'OCR."""
    text = str(value).strip()
    if len(text) < 12:
        return False
    if GARBLE_REPEAT_RE.search(text):
        return True
    letters = sum(c.isalpha() or c.isspace() or c.isdigit() for c in text)
    return letters / len(text) < 0.75


# --- ancrage dans le texte source -------------------------------------------

def check_grounded(field: str, value: object, source_text: str) -> FieldCheck | None:
    """Vérifie qu'une valeur recopiée existe bien dans la page.

    Ne s'applique qu'aux champs censés être *recopiés* (montants, références,
    nombres, dates) : pour un champ de synthèse comme ``objet``, le LLM
    reformule légitimement, et exiger l'ancrage littéral produirait des faux
    positifs en masse. Retourne ``None`` quand le contrôle ne s'applique pas.
    """
    if is_placeholder(value) or not source_text:
        return None
    if isinstance(value, (list, dict, bool)):
        # Un conteneur n'est pas une valeur recopiée : concaténer les chiffres
        # de ses éléments fabriquerait des nombres qui n'existent nulle part.
        return None
    if not re.search(r"montant|numero|reference|nombre|date|delai|telephone|frais", field):
        return None

    haystack = re.sub(r"\D", "", source_text)
    needles = [re.sub(r"\D", "", d) for d in NUMBER_RE.findall(normalize(str(value)))]
    needles = [n for n in needles if len(n) >= 3]  # 2 chiffres : trop de faux positifs
    if not needles:
        return None

    missing = [n for n in needles if n not in haystack]
    if not missing:
        return FieldCheck(field, Status.OK, "valeur retrouvée dans le texte de la page")
    return FieldCheck(
        field,
        Status.UNGROUNDED,
        f"les chiffres {', '.join(missing)} n'apparaissent pas dans le texte de la page",
        hint="Hallucination probable du modèle, ou chiffres mutilés par l'OCR : "
             "comparer avec le PDF à la page indiquée.",
        evidence=missing,
    )


# --- contrôles par type de champ --------------------------------------------

def _check_amount(field: str, value: object) -> FieldCheck:
    text = str(value)
    amount = parse_amount(text)
    if amount is None:
        return FieldCheck(field, Status.INVALID, f"aucun nombre exploitable dans « {text[:60]} »",
                          hint="Le modèle a recopié du texte au lieu d'un montant.")
    if not CURRENCY_RE.search(normalize(text)) and amount < MIN_PLAUSIBLE_AMOUNT:
        return FieldCheck(field, Status.SUSPECT, f"montant {amount} sans devise et anormalement bas",
                          normalized=amount, hint="Séparateurs de milliers probablement perdus par l'OCR.")
    if amount < MIN_PLAUSIBLE_AMOUNT:
        return FieldCheck(field, Status.SUSPECT, f"montant {amount} F CFA implausible (< {MIN_PLAUSIBLE_AMOUNT})",
                          normalized=amount)
    if amount > MAX_PLAUSIBLE_AMOUNT:
        return FieldCheck(field, Status.SUSPECT, f"montant {amount} implausible (trop élevé)", normalized=amount,
                          hint="Deux montants ont probablement été concaténés.")
    return FieldCheck(field, Status.OK, normalized=amount)


# L'ouverture des plis est très souvent fixée non pas à une date, mais par
# rapport au dépôt : « sera faite immédiatement en présence des candidats ».
# C'est une échéance réglementaire valable, et la refuser ferait crier une
# alerte sur les trois quarts des annonces du bulletin.
RELATIVE_DEADLINE_RE = re.compile(
    r"imm[ée]diatement|aussit[ôo]t|dans\s+la\s+foul[ée]e|[àa]\s+la\s+m[êe]me\s+heure"
    r"|le\s+m[êe]me\s+jour|s[ée]ance\s+publique"
)


def _check_date(field: str, value: object) -> FieldCheck:
    text = str(value)
    parsed = parse_date(text)
    if parsed is None and RELATIVE_DEADLINE_RE.search(normalize(text)):
        return FieldCheck(
            field, Status.OK, normalized="relatif: à la suite du dépôt",
            reason="échéance exprimée par rapport au dépôt, non par une date",
        )
    if parsed is None:
        return FieldCheck(field, Status.INVALID, f"date illisible dans « {text[:60]} »",
                          hint="Vérifier la ligne correspondante : l'OCR coupe souvent le mois.")
    year, month, day = parsed
    if not MIN_PLAUSIBLE_YEAR <= year <= MAX_PLAUSIBLE_YEAR:
        return FieldCheck(field, Status.SUSPECT, f"année {year} hors plage plausible",
                          normalized=f"{year:04d}-{month:02d}-{day:02d}")
    check = FieldCheck(field, Status.OK, normalized=f"{year:04d}-{month:02d}-{day:02d}")
    if "limite" in field or "ouverture" in field:
        if not TIME_RE.search(normalize(text)):
            # L'heure est réglementairement indiquée : son absence trahit une
            # extraction partielle de la ligne, pas une annonce sans heure.
            return FieldCheck(field, Status.SUSPECT, "date sans heure de dépôt/ouverture",
                              normalized=check.normalized,
                              hint="L'heure figure normalement sur la même ligne : extraction tronquée.")
    return check


def _check_reference(field: str, value: object) -> FieldCheck:
    text = normalize(str(value))
    if not re.search(r"\d", text):
        return FieldCheck(field, Status.INVALID, "référence sans aucun chiffre")
    if not REFERENCE_RE.search(text):
        return FieldCheck(field, Status.SUSPECT, f"forme inhabituelle pour une référence : « {str(value)[:60]} »",
                          hint="Les références du bulletin suivent « N°AAAA-NN/SIGLE/... ».")
    return FieldCheck(field, Status.OK, normalized=str(value).strip())


def _check_count(field: str, value: object) -> FieldCheck:
    amount = parse_amount(str(value))
    if amount is None:
        return FieldCheck(field, Status.INVALID, f"nombre illisible : « {str(value)[:40]} »")
    if amount > 500:
        return FieldCheck(field, Status.SUSPECT, f"{amount} plis reçus : valeur improbable", normalized=amount)
    return FieldCheck(field, Status.OK, normalized=amount)


def _check_delai(field: str, value: object) -> FieldCheck:
    text = normalize(str(value))
    m = DELAI_RE.search(text)
    if not m:
        return FieldCheck(field, Status.SUSPECT, f"délai sans quantité ni unité : « {str(value)[:40]} »")
    return FieldCheck(field, Status.OK, normalized=f"{m.group(1)} {m.group(2)}")


def _check_phones(field: str, value: object) -> FieldCheck:
    values = value if isinstance(value, list) else [value]
    bad = []
    for v in values:
        digits = re.sub(r"\D", "", str(v))
        digits = digits[3:] if digits.startswith("226") else digits
        if len(digits) != 8:
            bad.append(str(v))
    if bad:
        return FieldCheck(field, Status.SUSPECT, f"numéro(s) hors format burkinabè (8 chiffres) : {', '.join(bad[:3])}",
                          hint="Chiffres probablement collés ou perdus à l'OCR.")
    return FieldCheck(field, Status.OK)


def _check_text(field: str, value: object) -> FieldCheck:
    text = str(value).strip()
    if looks_garbled(text):
        return FieldCheck(field, Status.SUSPECT, "texte dégradé (bruit OCR probable)",
                          hint="Relire la page source : la zone est mal numérisée.")
    if len(normalize(text)) < 4:
        return FieldCheck(field, Status.SUSPECT, f"valeur anormalement courte : « {text} »")
    return FieldCheck(field, Status.OK, normalized=text)


def check_field(field: str, value: object, source_text: str = "") -> FieldCheck:
    """Contrôle complet d'un champ : présence, forme, plausibilité, ancrage.

    Le choix du contrôle se fait sur le *nom* du champ, pour qu'un champ ajouté
    dans ``schema_config.yaml`` soit validé sans toucher au code dès lors qu'il
    suit la convention de nommage (``montant_*``, ``date_*``, ``delai_*``...).
    """
    if is_placeholder(value):
        return FieldCheck(field, Status.MISSING, "absent ou valeur de remplissage")

    name = field.lower()
    if isinstance(value, bool):
        check = FieldCheck(field, Status.OK, normalized=value)
    elif "telephone" in name:
        # Avant le cas conteneur : les téléphones arrivent en liste et ont
        # leur propre format à contrôler élément par élément.
        check = _check_phones(field, value)
    elif isinstance(value, (list, dict)):
        check = FieldCheck(field, Status.OK, normalized=f"{len(value)} entrée(s)")
    elif re.search(r"montant|frais|cout|garantie", name):
        check = _check_amount(field, value)
    elif name.startswith("date") or "date" in name:
        check = _check_date(field, value)
    elif "delai" in name:
        check = _check_delai(field, value)
    elif "numero" in name or "reference" in name:
        check = _check_reference(field, value)
    elif "nombre" in name or "plis" in name:
        check = _check_count(field, value)
    else:
        check = _check_text(field, value)

    if check.ok:
        grounded = check_grounded(field, value, source_text)
        if grounded is not None and not grounded.ok:
            return grounded
    return check


def check_record(record: dict, fields: list[str], source_text: str = "") -> dict[str, FieldCheck]:
    """Contrôle tous les champs attendus d'une annonce extraite."""
    return {f: check_field(f, record.get(f), source_text) for f in fields}
