"""
Hiérarchie des AVIS → SOUS-UNITÉS nommées.

Deux niveaux (ordre du bulletin DGCMEF) :
  A. macro  : MINISTERES (institutions, maîtrises d'ouvrages déléguées) puis REGIONS
  B. catégorie : travaux, Fournitures et Services courants, Prestations intellectuelles

Chaque couple (macro, catégorie) forme une SOUS-UNITÉ, nommée
  avis-<macro>-<categorie>-<numero>-<date>
p. ex. « avis-regions-travaux-N4503-20261016 ».

group_sub_units() regroupe les blocs d'annonces (déjà étiquetés _macro_section /
_categorie par la segmentation couleur) en sous-unités ORDONNÉES, chacune avec
son nom, ses pages, son nombre d'annonces, et marque chaque annonce de son
_sous_unite. Pur / testable hors-ligne.
"""

import re
from typing import Optional

# Ordre canonique de traitement (ministères d'abord, puis régions ; dans chaque
# macro : travaux, fournitures, prestations intellectuelles).
MACRO_ORDER = ["MINISTERES", "REGIONS"]
CAT_ORDER = ["travaux", "fournitures", "prestations_intellectuelles"]

_MACRO_SLUG = {"MINISTERES": "ministeres", "REGIONS": "regions"}
_CAT_SLUG = {"travaux": "travaux", "fournitures": "fournitures",
             "prestations_intellectuelles": "prestations-intellectuelles"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "autre"


def sub_unit_name(macro: Optional[str], categorie: Optional[str],
                  numero: str, date: str) -> str:
    """Nom canonique d'une sous-unité : avis-<macro>-<categorie>-<numero>-<date>."""
    m = _MACRO_SLUG.get(macro, _slug(macro) if macro else "autre")
    c = _CAT_SLUG.get(categorie, _slug(categorie) if categorie else "autre")
    return f"avis-{m}-{c}-{numero}-{date}"


def _rank(macro: Optional[str], categorie: Optional[str]) -> tuple:
    """Clé de tri selon l'ordre canonique (inconnus rejetés en fin)."""
    mi = MACRO_ORDER.index(macro) if macro in MACRO_ORDER else len(MACRO_ORDER)
    ci = CAT_ORDER.index(categorie) if categorie in CAT_ORDER else len(CAT_ORDER)
    return (mi, ci)


def group_sub_units(avis: list, numero: str, date: str) -> list:
    """Regroupe les annonces par (macro, catégorie) en sous-unités ordonnées.

    Mute chaque annonce en lui posant `_sous_unite` (son nom). Retourne la liste
    des sous-unités : {nom, macro, categorie, nb_annonces, pages, indices,
    nb_triplet_incomplet}."""
    groups = {}
    for idx, a in enumerate(avis):
        macro = a.get("_macro_section")
        cat = a.get("_categorie")
        key = (macro, cat)
        nom = sub_unit_name(macro, cat, numero, date)
        a["_sous_unite"] = nom
        g = groups.setdefault(key, {
            "nom": nom, "macro": macro, "categorie": cat,
            "indices": [], "pages": [], "nb_triplet_incomplet": 0,
        })
        g["indices"].append(idx)
        pd, pf = a.get("_page_debut"), a.get("_page_fin")
        if pd:
            g["pages"].append(pd)
        if pf:
            g["pages"].append(pf)
        if a.get("_triplet_complet") is False:
            g["nb_triplet_incomplet"] += 1

    out = []
    for key in sorted(groups, key=lambda k: _rank(*k)):
        g = groups[key]
        pages = g["pages"]
        out.append({
            "nom": g["nom"], "macro": g["macro"], "categorie": g["categorie"],
            "nb_annonces": len(g["indices"]),
            "pages": [min(pages), max(pages)] if pages else None,
            "nb_triplet_incomplet": g["nb_triplet_incomplet"],
        })
    return out
