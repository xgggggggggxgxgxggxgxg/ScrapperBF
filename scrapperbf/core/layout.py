"""Lecture de la maquette : les bandes colorées délimitent les annonces.

Le Quotidien compose chaque avis avec un en-tête de trois bandes de couleur
franche, et cette maquette est un signal **explicite** du metteur en page — pas
une heuristique. Elle est donc plus fiable que la taille de police, et c'est
elle qui délimite les annonces :

=========================  ==================  ====================================
Bande                      Rôle                Exemples relevés dans les bulletins
=========================  ==================  ====================================
rouge ``(1, 0, 0)``        catégorie           Travaux · Fournitures et Services
                                               courants · Prestations
                                               intellectuelles
jaune ``(1, 1, 0)``        émetteur            REGION DU GUIRIKO · COMMUNE DE
                                               KOUMBIA · OFFICE NATIONAL DE LA
                                               SECURITE ROUTIERE
cyan ``(0, 1, 1)``         objet de l'annonce  « Réalisation et réhabilitation
                                               d'infrastructures hydrauliques… »
noir ``(0, 0, 0)``         institution         en section RÉSULTATS uniquement
=========================  ==================  ====================================

Deux pièges, constatés sur les bulletins réels et traités ici :

1. **le jaune sert aussi aux en-têtes de tableaux** de la section Résultats
   (« Soumissionnaires », « Montant TTC »). Le discriminant est la largeur :
   une bande d'annonce occupe toute la justification (~520 pt), une cellule de
   tableau fait 40 à 135 pt ;
2. **chaque bande est dessinée deux fois** (le fond, puis le filet de bordure),
   à quelques dixièmes de point près. Sans déduplication, chaque annonce est
   comptée deux fois.

Une nouvelle annonce commence à **chaque bande jaune** ; sa catégorie est celle
de la dernière bande rouge rencontrée.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from .text import normalize, preview


class Role(str, Enum):
    """Rôle d'une bande colorée dans la maquette."""

    CATEGORIE = "CATEGORIE"
    EMETTEUR = "EMETTEUR"
    OBJET = "OBJET"
    INSTITUTION = "INSTITUTION"   # bandeau noir de la section Résultats

    @property
    def marker(self) -> str:
        """Balise écrite dans le Markdown pour tracer l'origine du titre."""
        return f"[{self.value}]"


# Couleurs de la maquette, en RVB normalisé tel que rendu par le moteur PDF.
ROLE_BY_COLOR: dict[tuple[float, float, float], Role] = {
    (1.0, 0.0, 0.0): Role.CATEGORIE,
    (1.0, 1.0, 0.0): Role.EMETTEUR,
    (0.0, 1.0, 1.0): Role.OBJET,
    (0.0, 0.0, 0.0): Role.INSTITUTION,
}

# Largeur minimale par rôle, en points. La catégorie est composée sur une
# demi-justification centrée ; émetteur et objet occupent toute la largeur.
MIN_WIDTH: dict[Role, float] = {
    Role.CATEGORIE: 150.0,
    Role.EMETTEUR: 400.0,
    Role.OBJET: 400.0,
    Role.INSTITUTION: 400.0,
}
MIN_HEIGHT, MAX_HEIGHT = 8.0, 60.0
# Tolérance de regroupement du fond et de son filet de bordure.
DEDUP_Y_TOLERANCE = 1.5

# Portée de l'émetteur : l'État et ses établissements d'un côté, les
# collectivités de l'autre. C'est la distinction « simples » / « régionaux ».
NATIONAL_RE = re.compile(
    r"ministere|societe nationale|office national|direction (generale|provinciale)"
    r"|agence|universite|institut|caisse|fonds|autorite|conseil|presidence|assemblee"
    r"|secretariat|bureau|projet|programme|centre hospitalier|chu|chr|enafa|sonatur"
    r"|sonabel|lonab|onea|onaser|loterie|ecole nationale|hopital"
)
LOCAL_RE = re.compile(r"region|commune|mairie|province|conseil regional|arrondissement|village")


# Les trois catégories du bulletin. La bande rouge les imprime, mais elle est
# composée à la main : le N°4505 porte « Furnitures et Services courants ».
# Une faute de frappe ne doit pas faire perdre son profil de champs à une
# annonce, donc le libellé lu est rapproché de sa forme canonique.
CATEGORIES = (
    "Fournitures et Services courants",
    "Travaux",
    "Prestations intellectuelles",
)
CATEGORY_MATCH_CUTOFF = 0.82


def canonical_category(label: str | None) -> str | None:
    """Ramène un libellé de catégorie à sa forme canonique, fautes comprises.

    Retourne ``None`` si le libellé ne ressemble à aucune catégorie connue :
    inventer un rattachement serait pire que d'avouer l'inconnu.
    """
    if not label:
        return None
    lu = normalize(label)
    for canonique in CATEGORIES:
        if lu == normalize(canonique):
            return canonique
    proches = difflib.get_close_matches(
        lu, [normalize(c) for c in CATEGORIES], n=1, cutoff=CATEGORY_MATCH_CUTOFF
    )
    if not proches:
        return None
    return next(c for c in CATEGORIES if normalize(c) == proches[0])


@dataclass
class Band:
    """Une bande colorée et le texte qu'elle porte."""

    role: Role
    text: str
    page: int
    y0: float
    y1: float
    width: float
    height: float

    def to_dict(self) -> dict:
        return {
            "role": self.role.value,
            "text": self.text,
            "page": self.page,
            "y0": round(self.y0, 1),
            "width": round(self.width, 1),
            "height": round(self.height, 1),
        }


@dataclass
class NoticeHeader:
    """En-tête d'annonce reconstitué depuis les bandes."""

    emetteur: str
    categorie: str | None
    objet: list[str] = field(default_factory=list)
    page_debut: int = 0
    page_fin: int = 0

    @property
    def objet_text(self) -> str:
        return " ".join(self.objet).strip()

    @property
    def categorie_canonique(self) -> str | None:
        """Catégorie ramenée à sa forme de référence (fautes de frappe comprises)."""
        return canonical_category(self.categorie)

    @property
    def portee(self) -> str:
        """« NATIONAL » (appel d'offres simple) ou « REGIONAL » (collectivité).

        L'émetteur tranche : c'est son statut qui fait la portée, et les deux
        familles de libellés ne se recouvrent pas dans les bulletins observés.
        Un libellé portant les deux (« Direction Provinciale de … ») est classé
        national, car c'est un service déconcentré de l'État.
        """
        texte = normalize(self.emetteur)
        if NATIONAL_RE.search(texte):
            return "NATIONAL"
        if LOCAL_RE.search(texte):
            return "REGIONAL"
        return "INDETERMINE"

    def to_dict(self) -> dict:
        return {
            "emetteur": self.emetteur,
            "categorie": self.categorie,
            "categorie_canonique": self.categorie_canonique,
            "objet": self.objet_text,
            "portee": self.portee,
            "page_debut": self.page_debut,
            "page_fin": self.page_fin,
        }


class LayoutBackend(Protocol):
    """Ce qu'il faut d'un PDF pour lire sa maquette."""

    def page_count(self, path: str) -> int: ...
    def page_fills(self, path: str, index: int) -> list[dict]: ...
    def page_words(self, path: str, index: int) -> list[tuple]: ...


def _text_inside(words: list[tuple], x0: float, y0: float, x1: float, y1: float) -> str:
    """Mots dont le centre tombe dans le rectangle, dans l'ordre de lecture."""
    dedans = [
        w for w in words
        if x0 - 2 <= (w[0] + w[2]) / 2 <= x1 + 2 and y0 - 2 <= (w[1] + w[3]) / 2 <= y1 + 2
    ]
    dedans.sort(key=lambda w: (round(w[1]), w[0]))
    return " ".join(w[4] for w in dedans).strip()


def bands_on_page(fills: list[dict], words: list[tuple], page_number: int) -> list[Band]:
    """Bandes d'une page, dédupliquées et triées en ordre de lecture."""
    candidates: list[Band] = []
    for fill in fills:
        color = fill.get("color")
        rect = fill.get("rect")
        if color is None or rect is None:
            continue
        role = ROLE_BY_COLOR.get(tuple(round(float(c), 2) for c in color))
        if role is None:
            continue
        x0, y0, x1, y1 = rect
        width, height = x1 - x0, y1 - y0
        if not (MIN_HEIGHT <= height <= MAX_HEIGHT) or width < MIN_WIDTH[role]:
            continue
        text = _text_inside(words, x0, y0, x1, y1)
        if not text:
            continue
        candidates.append(Band(role, text, page_number, y0, y1, width, height))

    # Déduplication : le fond et son filet partagent le même y au dixième près.
    # On garde la bande la plus large, qui porte le texte le plus complet.
    retenues: dict[tuple[int, Role], Band] = {}
    for band in sorted(candidates, key=lambda b: (b.y0, -b.width)):
        cle = (int(band.y0 / DEDUP_Y_TOLERANCE), band.role)
        if cle not in retenues:
            retenues[cle] = band
    return sorted(retenues.values(), key=lambda b: (b.y0, b.width))


def extract_bands(path: str, backend: LayoutBackend) -> list[Band]:
    """Toutes les bandes du document, en ordre de lecture."""
    bands: list[Band] = []
    for index in range(backend.page_count(path)):
        fills = backend.page_fills(path, index)
        words = backend.page_words(path, index)
        bands.extend(bands_on_page(fills, words, index + 1))
    return bands


def group_notices(bands: list[Band]) -> list[NoticeHeader]:
    """Regroupe les bandes en en-têtes d'annonces.

    La bande jaune (émetteur) ouvre l'annonce ; les bandes cyan qui suivent en
    composent l'objet, qui tient parfois sur deux lignes donc sur deux bandes.
    """
    notices: list[NoticeHeader] = []
    categorie: str | None = None
    courante: NoticeHeader | None = None

    for band in bands:
        if band.role is Role.CATEGORIE:
            categorie = band.text
        elif band.role is Role.EMETTEUR:
            courante = NoticeHeader(
                emetteur=band.text, categorie=categorie,
                page_debut=band.page, page_fin=band.page,
            )
            notices.append(courante)
        elif band.role is Role.OBJET and courante is not None:
            courante.objet.append(band.text)
            courante.page_fin = max(courante.page_fin, band.page)
    return notices


def bands_to_markdown(bands: list[Band]) -> dict[int, list[str]]:
    """Rend les bandes d'une page en titres Markdown, rôle balisé.

    La balise (``[CATEGORIE]``, ``[EMETTEUR]``, ``[OBJET]``) transporte la
    preuve de couleur jusqu'à la classification des titres, qui peut alors
    décider sans deviner.
    """
    par_page: dict[int, list[str]] = {}
    for band in bands:
        level = 1 if band.role in (Role.CATEGORIE, Role.INSTITUTION) else 2
        par_page.setdefault(band.page, []).append(
            f"{'#' * level} {band.role.marker} {band.text}"
        )
    return par_page


def default_backend():
    """Backend PyMuPDF, ou ``None`` si la bibliothèque n'est pas installée."""
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError:
            return None

    class _PyMuPdfLayout:
        def page_count(self, path: str) -> int:
            with pymupdf.open(path) as doc:
                return doc.page_count

        def page_fills(self, path: str, index: int) -> list[dict]:
            with pymupdf.open(path) as doc:
                page = doc[index]
                out = []
                for d in page.get_drawings():
                    if d.get("fill") is None:
                        continue
                    r = d["rect"]
                    out.append({"color": tuple(d["fill"]), "rect": (r.x0, r.y0, r.x1, r.y1)})
                return out

        def page_words(self, path: str, index: int) -> list[tuple]:
            with pymupdf.open(path) as doc:
                return doc[index].get_text("words")

    return _PyMuPdfLayout()


def describe(notices: list[NoticeHeader]) -> str:
    """Index lisible des annonces d'un bulletin."""
    lignes = [f"{len(notices)} annonces"]
    for i, n in enumerate(notices, start=1):
        pages = f"p{n.page_debut}" + (f"-{n.page_fin}" if n.page_fin != n.page_debut else "")
        lignes.append(
            f"[{i:3}] {pages:>9} {n.portee:11} {str(n.categorie or '?')[:32]:34} {n.emetteur[:40]}"
        )
        if n.objet:
            lignes.append(f"{'':16} → {preview(n.objet_text, 96)}")
    return "\n".join(lignes)
