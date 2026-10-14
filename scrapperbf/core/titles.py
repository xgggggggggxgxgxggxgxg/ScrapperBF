"""Classification des titres — point d'entrée du tri des annonces.

Le titre est l'information la plus fiable d'une annonce : il est court, il est
mis en forme par la rédaction du bulletin, et il survit mieux à l'OCR qu'un
tableau. On s'en sert donc comme discriminant principal :

    titre  ->  nature de l'annonce  ->  champs attendus et leur priorité

Chaque classification retourne ses *preuves* (les motifs qui ont déclenché), de
sorte que le dashboard puisse expliquer à l'humain pourquoi une annonce a été
rangée là, et qu'un faux positif soit corrigible sans lire le code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .text import normalize


class TitleKind(str, Enum):
    """Nature d'un titre rencontré dans le flux du bulletin."""

    RESULTATS = "RESULTATS"                      # synthèse / résultats provisoires
    DEMANDE_PRIX = "DEMANDE_PRIX"
    APPEL_OFFRES = "APPEL_OFFRES"
    MANIFESTATION_INTERET = "MANIFESTATION_INTERET"
    DEMANDE_PROPOSITIONS = "DEMANDE_PROPOSITIONS"
    AVIS_ATTRIBUTION = "AVIS_ATTRIBUTION"
    RECTIFICATIF = "RECTIFICATIF"
    ANNULATION = "ANNULATION"
    SECTION_MARKER = "SECTION_MARKER"            # "Travaux", "Fournitures et Services courants"
    CATEGORY_MARKER = "CATEGORY_MARKER"          # "MINISTERES ET INSTITUTIONS", "COLLECTIVITES"
    OBJET = "OBJET"                              # objet de marché ("Acquisition de ...")
    AUTORITE = "AUTORITE"                        # en-tête d'institution
    UNKNOWN = "UNKNOWN"


# Les sections métier du schéma (schema_config.yaml) auxquelles chaque nature
# se rattache. Sert à choisir le bon jeu de champs à extraire.
SECTION_OF_KIND: dict[TitleKind, str] = {
    TitleKind.RESULTATS: "RESULTATS",
    TitleKind.AVIS_ATTRIBUTION: "RESULTATS",
    TitleKind.DEMANDE_PRIX: "AVIS",
    TitleKind.APPEL_OFFRES: "AVIS",
    TitleKind.MANIFESTATION_INTERET: "AVIS",
    TitleKind.DEMANDE_PROPOSITIONS: "AVIS",
    TitleKind.RECTIFICATIF: "AVIS",
    TitleKind.ANNULATION: "AVIS",
    TitleKind.OBJET: "AVIS",
}

# (nature, poids, motif). Les poids additionnent les preuves : un titre qui
# cumule plusieurs indices d'une même nature est classé avec plus de confiance.
#
# Les natures *qualifiantes* (annulation, rectificatif, synthèse de résultats)
# portent un poids supérieur aux natures de procédure, parce qu'elles citent
# normalement la procédure qu'elles visent : « avis d'annulation de l'appel
# d'offres N°... » est une annulation, pas un appel d'offres.
_RULES: list[tuple[TitleKind, float, re.Pattern[str]]] = [
    (TitleKind.RESULTATS, 1.0, re.compile(r"resultats?\s+provisoires?")),
    (TitleKind.RESULTATS, 1.4, re.compile(r"synthese\s+(des?\s+)?(resultats?|offres?)")),
    (TitleKind.RESULTATS, 0.6, re.compile(r"proces[\s-]verbal\s+(de\s+)?deliberation")),
    (TitleKind.AVIS_ATTRIBUTION, 1.4, re.compile(r"avis\s+d.attribution")),
    (TitleKind.DEMANDE_PRIX, 1.0, re.compile(r"(avis\s+de\s+)?demande\s+de\s+prix")),
    (TitleKind.DEMANDE_PRIX, 0.5, re.compile(r"\bdpx\b")),
    (TitleKind.APPEL_OFFRES, 1.0, re.compile(r"appels?\s+d.offres?")),
    (TitleKind.APPEL_OFFRES, 0.4, re.compile(r"\b(aoo|aoa|aoi)\b")),
    (TitleKind.MANIFESTATION_INTERET, 1.0, re.compile(r"manifestation\s+d.inter[eê]ts?")),
    (TitleKind.MANIFESTATION_INTERET, 0.4, re.compile(r"\bami\b")),
    (TitleKind.DEMANDE_PROPOSITIONS, 1.0, re.compile(r"demande\s+de\s+propositions?")),
    (TitleKind.DEMANDE_PROPOSITIONS, 0.5, re.compile(r"\bdp\b.*consultant")),
    (TitleKind.RECTIFICATIF, 1.4, re.compile(r"rectificatif|additif")),
    (TitleKind.ANNULATION, 1.4, re.compile(r"annulation|infructueux")),
]

# Marqueurs de structure : ils ne décrivent pas un marché, ils séparent le
# bulletin. Ancrés en entier pour ne pas confondre « Travaux » (marqueur) avec
# « Travaux de construction de ... » (objet).
_SECTION_MARKERS: dict[str, str] = {
    r"^fournitures?\s+et\s+services?\s+courants?$": "Fournitures et Services courants",
    r"^travaux$": "Travaux",
    r"^prestations?\s+intellectuelles?$": "Prestations intellectuelles",
}
_CATEGORY_MARKERS: dict[str, str] = {
    r"ministeres?\s+et\s+institutions|maitrises?\s+d.ouvrages?\s+deleguees?": "MINISTERES",
    r"collectivites?\s+territoriales?": "COLLECTIVITES",
}

# Verbes d'action administrative ouvrant un objet de marché.
_OBJET_RE = re.compile(
    r"^(acquisition|travaux|realisation|fourniture|entretien|construction|constitution"
    r"|rehabilitation|amenagement|approvisionnement|location|recrutement|prestation"
    r"|elaboration|etude|audit|formation|assistance|controle|suivi|selection"
    r"|consultation|mission|achat|livraison|confection|installation|maintenance"
    r"|gardiennage|nettoyage|transport|edition|impression)\b"
)

# En-tête d'institution : essentiellement en majuscules dans le document.
_AUTORITE_RE = re.compile(
    r"\b(ministere|region|commune|conseil\s+regional|mairie|universite|institut"
    r"|direction\s+generale|agence|office|societe\s+nationale|caisse|hopital"
    r"|centre\s+hospitalier|ecole\s+nationale|secretariat\s+general|presidence"
    r"|assemblee|autorite|fonds|projet|programme|bureau)\b"
)

_TYPE_TRAVAUX_RE = re.compile(r"^(travaux|construction|realisation|rehabilitation|amenagement)\b")
_TYPE_PRESTATIONS_RE = re.compile(
    r"prestation|manifestation d.inter[eê]t|consultant|etude|audit|assistance|controle|suivi|formation"
)


@dataclass
class TitleAnalysis:
    """Résultat du tri d'un titre, avec traçabilité."""

    kind: TitleKind
    confidence: float
    evidence: list[str] = field(default_factory=list)
    section: str | None = None       # "AVIS" | "RESULTATS"
    marche_type: str | None = None   # Travaux / Fournitures / Prestations
    category: str | None = None      # MINISTERES / COLLECTIVITES
    marker_value: str | None = None  # valeur du marqueur de structure, le cas échéant

    @property
    def is_structural(self) -> bool:
        """Vrai pour un marqueur de mise en page, qui ne porte pas d'annonce."""
        return self.kind in (TitleKind.SECTION_MARKER, TitleKind.CATEGORY_MARKER)

    @property
    def starts_notice(self) -> bool:
        """Vrai si ce titre peut ouvrir une nouvelle annonce."""
        return self.kind in SECTION_OF_KIND


def analyze_title(raw_title: str) -> TitleAnalysis:
    """Classe un titre et déduit section, type de marché et catégorie."""
    text = normalize(raw_title)
    if not text:
        return TitleAnalysis(TitleKind.UNKNOWN, 0.0, ["titre vide"])

    for pattern, value in _CATEGORY_MARKERS.items():
        if re.search(pattern, text):
            return TitleAnalysis(
                TitleKind.CATEGORY_MARKER, 0.95, [f"marqueur de catégorie « {value} »"],
                category=value, marker_value=value,
            )

    for pattern, value in _SECTION_MARKERS.items():
        if re.match(pattern, text):
            return TitleAnalysis(
                TitleKind.SECTION_MARKER, 0.95, [f"marqueur de section « {value} »"],
                marche_type=value, marker_value=value,
            )

    scores: dict[TitleKind, float] = {}
    evidence: dict[TitleKind, list[str]] = {}
    for kind, weight, pattern in _RULES:
        m = pattern.search(text)
        if m:
            scores[kind] = scores.get(kind, 0.0) + weight
            evidence.setdefault(kind, []).append(f"« {m.group(0)} » → {kind.value}")

    if scores:
        kind = max(scores, key=lambda k: scores[k])
        # Plafonné à 0.98 : un titre reste une heuristique, jamais une certitude.
        confidence = min(0.98, 0.55 + 0.35 * scores[kind])
        return TitleAnalysis(
            kind, round(confidence, 3), evidence[kind],
            section=SECTION_OF_KIND.get(kind),
            marche_type=infer_marche_type(raw_title),
        )

    if _OBJET_RE.match(text):
        return TitleAnalysis(
            TitleKind.OBJET, 0.7, ["commence par un verbe d'action administrative"],
            section="AVIS", marche_type=infer_marche_type(raw_title),
        )

    if _AUTORITE_RE.search(text):
        return TitleAnalysis(TitleKind.AUTORITE, 0.6, ["nom d'institution reconnu"])

    return TitleAnalysis(TitleKind.UNKNOWN, 0.15, ["aucun motif connu"])


def infer_marche_type(raw_title: str) -> str | None:
    """Type de marché déduit de l'objet, quand le marqueur de section manque."""
    text = normalize(raw_title)
    if _TYPE_PRESTATIONS_RE.search(text):
        return "Prestations intellectuelles"
    if _TYPE_TRAVAUX_RE.match(text):
        return "Travaux"
    if re.match(r"^(acquisition|fourniture|achat|livraison|approvisionnement|location|entretien)\b", text):
        return "Fournitures et Services courants"
    return None
