"""Normalisation de texte partagée par toutes les couches QA.

Tout ce qui compare, hache ou cherche des motifs passe par ici : une seule
définition de « même texte » évite que la détection de doublons et le scoring
ne soient en désaccord sur ce qu'est un caractère significatif.
"""

from __future__ import annotations

import re
import unicodedata

MARKUP_RE = re.compile(r"</?(?:mark|b|i|u|em|strong|sup|sub)>", re.IGNORECASE)
MD_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_|`)")
IMAGE_PLACEHOLDER_RE = re.compile(r"<!--\s*image\s*-->", re.IGNORECASE)
MULTISPACE_RE = re.compile(r"[ \t  ]+")
NON_WORD_RE = re.compile(r"[^0-9a-z]+")


def strip_markup(text: str) -> str:
    """Retire balises HTML résiduelles, emphase Markdown et placeholders image."""
    out = IMAGE_PLACEHOLDER_RE.sub(" ", text)
    out = MARKUP_RE.sub("", out)
    out = MD_EMPHASIS_RE.sub("", out)
    return MULTISPACE_RE.sub(" ", out).strip()


def fold_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def normalize(text: str) -> str:
    """Forme canonique pour comparaison : sans accents, minuscule, sans markup."""
    return MULTISPACE_RE.sub(" ", fold_accents(strip_markup(text)).lower()).strip()


def tokens(text: str) -> list[str]:
    """Tokens alphanumériques de la forme canonique, vides exclus."""
    return [t for t in NON_WORD_RE.split(normalize(text)) if t]


def shingles(text: str, size: int = 4) -> set[tuple[str, ...]]:
    """n-grammes de tokens, base de la similarité de Jaccard.

    Pour un texte plus court que `size`, on retourne le tuple complet afin que
    deux textes courts identiques restent comparables (sinon : ensemble vide
    des deux côtés, donc similarité indéfinie traitée à tort comme 0).
    """
    toks = tokens(text)
    if not toks:
        return set()
    if len(toks) <= size:
        return {tuple(toks)}
    return {tuple(toks[i : i + size]) for i in range(len(toks) - size + 1)}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def preview(text: str, width: int = 140) -> str:
    """Extrait d'une ligne, destiné à être lu par un humain dans une alerte."""
    flat = MULTISPACE_RE.sub(" ", strip_markup(text).replace("\n", " ")).strip()
    return flat if len(flat) <= width else flat[: width - 1].rstrip() + "…"
