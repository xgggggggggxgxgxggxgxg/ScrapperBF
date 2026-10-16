"""
Garde-fou FORMAT — contrôle déterministe AVANT extraction (géométrie = Python,
pas LLM, comme le recommande l'architecture : le code gère les pixels/pages, le
LLM lit le texte).

Pour chaque unité d'annonce issue de la segmentation, il décide :
  - l'annonce tient-elle sur UNE page ? sinon -> plage « xx-xx » ;
  - y a-t-il DEUX annonces dans le même bloc ? -> division automatique ;
  - le texte est-il corrompu (police cassée) ou le tableau renvoie-t-il des
    données douteuses ? -> BLOQUER l'extraction + alerte humaine ;
  - un en-tête d'avis et une référence sont-ils présents ? sinon -> bloquer.

Sortie : {valide, action: "extraire"|"diviser"|"bloquer", pages, nb_annonces,
sous_annonces, raisons, alerte_humaine}. L'appelant n'envoie au LLM que les
blocs « extraire » (ou chaque sous-annonce d'un « diviser »), et met les
« bloquer » dans la file de revue humaine.
"""

import re
from typing import Optional

from pdf_avis_extract import TYPE_PHRASE
from judge import garbling_score

# En-tête d'annonce = phrase de type SUIVIE d'une référence « n° … » : signature
# fiable d'un vrai début d'avis (le mot « demande de prix » seul réapparaît dans
# les clauses, mais pas « <type> n° »).
_HEADER_SIG_RE = re.compile(TYPE_PHRASE + r"\s*(?:à\s+commande?s?\s*)?n[°o]", re.I)
_REF_RE = re.compile(r"n[°o]\s*:?\s*\d{4}", re.I)
_TABLE_CELL_RE = re.compile(r"--(?:\s|\||$)")


def page_range(page_debut, page_fin) -> str:
    """« xx-xx » si l'annonce s'étale sur plusieurs pages, sinon « xx »."""
    if page_fin and page_debut and page_fin > page_debut:
        return f"{page_debut}-{page_fin}"
    return str(page_debut if page_debut is not None else "?")


def count_annonce_headers(text: str) -> int:
    return len(_HEADER_SIG_RE.findall(text or ""))


def table_bad_data(text: str) -> bool:
    """Heuristique « tableau qui renvoie de mauvaises données » : beaucoup de
    cellules vides « -- » ET texte peu lisible (typique d'un tableau de résultats
    mal aplati fourré dans un bloc d'avis)."""
    t = text or ""
    dashes = len(_TABLE_CELL_RE.findall(t))
    return dashes >= 6 and garbling_score(t) >= 0.25


def split_double(text: str) -> list:
    """Découpe un bloc contenant plusieurs annonces aux positions des en-têtes
    « <type> n° ». La 1re sous-annonce garde l'objet/émetteur qui précède le 1er
    en-tête ; les suivantes démarrent un peu avant leur en-tête pour inclure leur
    propre objet/émetteur."""
    starts = [m.start() for m in _HEADER_SIG_RE.finditer(text or "")]
    if len(starts) < 2:
        return [text]
    # bornes de découpe : juste avant chaque en-tête (sauf le 1er), avec un recul
    # de 160 car. pour récupérer l'objet/émetteur de la sous-annonce.
    cuts = [0] + [max(0, s - 160) for s in starts[1:]] + [len(text)]
    parts = []
    for i in range(len(cuts) - 1):
        seg = text[cuts[i]:cuts[i + 1]].strip()
        if seg:
            parts.append(seg)
    return parts


def validate_format(unit: dict) -> dict:
    """Garde-fou format sur une unité d'annonce (dict avec flat_text,
    page_debut, page_fin)."""
    text = unit.get("flat_text", "") or ""
    # Les deux conventions de clés coexistent (avis: _page_debut ; résultats: page_debut).
    pd = unit.get("page_debut", unit.get("_page_debut"))
    pf = unit.get("page_fin", unit.get("_page_fin"))
    pages = page_range(pd, pf)
    n_head = count_annonce_headers(text)
    garb = garbling_score(text)

    raisons = []
    action = "extraire"
    alerte = False
    sous = None

    # 1) Texte corrompu / tableau douteux -> bloquer + alerte (OCR requis).
    if garb >= 0.35:
        action = "bloquer"; alerte = True
        raisons.append(f"texte probablement corrompu (police/tableau), indice {garb} — OCR requis")
    elif table_bad_data(text):
        action = "bloquer"; alerte = True
        raisons.append("tableau renvoyant des données douteuses (cellules « -- » + texte peu lisible)")

    # 2) Deux annonces dans le même bloc -> division automatique.
    if n_head >= 2:
        sous = split_double(text)
        if action != "bloquer":
            action = "diviser"
        raisons.append(f"{n_head} annonces détectées dans le même bloc -> division en {len(sous)}")

    # 3) Aucun en-tête d'avis / aucune référence -> bloquer (bloc inexploitable).
    #    SAUF si l'annonce est déjà CONFIRMÉE par la couleur (bandeau objet cyan) :
    #    la géométrie prime, certains avis (additifs, commandes complémentaires)
    #    n'ont pas la phrase « Avis de demande de prix n° » mais sont de vrais avis.
    color_confirmed = bool(unit.get("_objet_bandeau"))
    if n_head == 0 and not _REF_RE.search(text) and not color_confirmed and action != "bloquer":
        action = "bloquer"; alerte = True
        raisons.append("aucun en-tête d'avis ni référence reconnus — bloc inexploitable")

    # 4) Multi-pages : simple information (jamais bloquant).
    if pf and pd and pf > pd:
        raisons.append(f"annonce sur plusieurs pages ({pages})")

    return {
        "valide": action == "extraire",
        "action": action,
        "pages": pages,
        "nb_annonces": max(1, n_head),
        "sous_annonces": sous,
        "raisons": raisons,
        "alerte_humaine": alerte,
    }
