"""Classement des annonces : secteur d'activité, région, type d'émetteur.

Le contexte d'usage est simple et c'est lui qui dicte ce module : un bulletin
fait soixante pages et quelques dizaines d'annonces, dont deux ou trois
intéressent un lecteur donné. Chercher à l'œil est fastidieux. Pour filtrer,
il faut donc des axes de classement, et ils doivent être au nombre de trois
seulement, car ce sont ceux sur lesquels un lecteur se décide :

* **le secteur** — « est-ce mon métier ? » (santé, informatique, BTP, …) ;
* **la région** — « est-ce que c'est près de moi ? » ;
* **le type d'émetteur** — « est-ce une mairie, un ministère, un projet ? ».

Le secteur est déduit de l'objet, pas de l'émetteur : un ministère de la Santé
qui achète des ordinateurs fait un marché informatique, et le lecteur qui vend
des ordinateurs doit le trouver. Chaque classement rend ses **preuves**, pour
qu'un lecteur puisse constater d'un coup d'œil pourquoi une annonce lui est
proposée, et qu'un classement douteux soit corrigible sans lire le code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .text import normalize


# Prime maximale accordée à un terme situé au tout début de l'objet. Elle
# décroît avec la position : une fenêtre fixe ne trancherait rien sur un objet
# court, où tous les termes seraient « en tête ».
HEAD_BONUS_MAX = 0.4


class Sector(str, Enum):
    """Secteur d'activité d'un marché, déduit de son objet."""

    SANTE = "SANTE"
    EDUCATION = "EDUCATION"
    INFORMATIQUE = "INFORMATIQUE"
    BTP = "BTP"
    EAU_ASSAINISSEMENT = "EAU_ASSAINISSEMENT"
    ENERGIE = "ENERGIE"
    AGRICULTURE = "AGRICULTURE"
    ELEVAGE = "ELEVAGE"
    ALIMENTATION = "ALIMENTATION"
    TRANSPORT = "TRANSPORT"
    MOBILIER_FOURNITURES = "MOBILIER_FOURNITURES"
    COMMUNICATION = "COMMUNICATION"
    SECURITE = "SECURITE"
    ENVIRONNEMENT = "ENVIRONNEMENT"
    ETUDES_CONSEIL = "ETUDES_CONSEIL"
    FINANCE_ASSURANCE = "FINANCE_ASSURANCE"
    MINES = "MINES"
    AUTRE = "AUTRE"

    @property
    def label(self) -> str:
        return {
            "SANTE": "Santé", "EDUCATION": "Éducation et formation",
            "INFORMATIQUE": "Informatique et télécoms", "BTP": "Bâtiment et travaux publics",
            "EAU_ASSAINISSEMENT": "Eau et assainissement", "ENERGIE": "Énergie",
            "AGRICULTURE": "Agriculture", "ELEVAGE": "Élevage et pêche",
            "ALIMENTATION": "Alimentation et restauration", "TRANSPORT": "Transport et véhicules",
            "MOBILIER_FOURNITURES": "Mobilier et fournitures", "COMMUNICATION": "Communication et médias",
            "SECURITE": "Sécurité et défense", "ENVIRONNEMENT": "Environnement",
            "ETUDES_CONSEIL": "Études, audit et conseil", "FINANCE_ASSURANCE": "Finance et assurance",
            "MINES": "Mines et géologie", "AUTRE": "Autre ou indéterminé",
        }[self.value]


# (secteur, poids, motif). Les poids s'additionnent : un objet qui cumule
# plusieurs indices d'un même secteur est classé avec plus de confiance. Les
# motifs les plus spécifiques portent le poids le plus fort, pour qu'un terme
# générique ne l'emporte pas sur un terme de métier.
_RULES: list[tuple[Sector, float, str]] = [
    (Sector.SANTE, 1.2, r"m[ée]dicaments?|pharmaceutique|vaccins?|consommables?\s+m[ée]dicaux"
                        r"|r[ée]actifs?|csps|cma\b|chr\b|chu\b|dispensaire|maternit[ée]"
                        r"|h[ôo]pital|hospitali|sanitaire|infirmi|laboratoire\s+d.analyses"),
    (Sector.SANTE, 0.8, r"sant[ée]|m[ée]dical|soins?|ambulance|incin[ée]rateur"),
    (Sector.EDUCATION, 1.2, r"\bceg\b|\bcsps\s+scolaire|[ée]cole|lyc[ée]e|coll[èe]ge|universit[ée]"
                            r"|salles?\s+de\s+classe|manuels?\s+scolaires?|fournitures?\s+scolaires?"
                            r"|tables?[\s-]bancs?|alphab[ée]tisation"),
    (Sector.EDUCATION, 0.7, r"enseignement|scolaire|[ée]ducation|formation\s+professionnelle|p[ée]dagogique"),
    (Sector.INFORMATIQUE, 1.2, r"informatiques?|logiciels?|licences?|ordinateurs?|serveurs?"
                               r"|imprimantes?|r[ée]seau\s+informatique|site\s+web|portail\s+web"
                               r"|application\s+(web|mobile)|biblioth[èe]que\s+virtuelle"
                               r"|num[ée]risation|t[ée]l[ée]phonie|internet|fibre\s+optique|biom[ée]tri"),
    (Sector.INFORMATIQUE, 0.7, r"num[ée]rique|digital|syst[èe]me\s+int[ée]gr[ée]|base\s+de\s+donn[ée]es"),
    (Sector.EAU_ASSAINISSEMENT, 1.3, r"forages?|\bpmh\b|ad(?:duction|uction)\s+d.eau|puits"
                                     r"|ch[âa]teau\s+d.eau|latrines?|assainissement|hydrauliques?"
                                     r"|caniveaux?|[ée]vacuation\s+des\s+eaux|potable"),
    # Noms de métier du bâtiment : ils désignent l'ouvrage lui-même.
    (Sector.BTP, 1.2, r"b[âa]timents?|mur\s+de\s+cl[ôo]ture|voiries?|pistes?\s+rurales?"
                      r"|pav[ée]|bitumage|ouvrages?\s+d.art|ponts?|dallots?|terrassement"
                      r"|g[ée]nie\s+civil|magasins?|hangars?|clinker|ciment|charpente|ma[çc]onnerie"),
    # Verbes d'action génériques : ils s'appliquent à tous les secteurs
    # (« réalisation d'un forage », « construction d'une école »). Les noter
    # comme un nom de métier ferait du bâtiment le secteur de tout le bulletin.
    (Sector.BTP, 0.85, r"construction|r[ée]habilitation|r[ée]fection|am[ée]nagement"),
    (Sector.BTP, 0.5, r"travaux|chantier|ma[îi]tr(?:ise|e)\s+d.œuvre"),
    (Sector.ENERGIE, 1.2, r"[ée]lectrification|[ée]lectricit[ée]|solaires?|photovolta[ïi]que"
                          r"|groupes?\s+[ée]lectrog[èe]nes?|lampadaires?|transformateurs?"
                          r"|hydrocarbures?|carburants?|gasoil|butane|panneaux\s+solaires?"),
    (Sector.AGRICULTURE, 1.2, r"semences?|engrais|intrants?\s+agricoles?|tracteurs?|motoculteurs?"
                              r"|charrues?|irrigation|p[ée]rim[èe]tres?\s+irrigu[ée]s?|agricoles?"
                              r"|phytosanitaires?|pesticides?|r[ée]colte|mara[îi]ch"),
    (Sector.ELEVAGE, 1.2, r"[ée]levage|b[ée]tail|bovins?|ovins?|caprins?|volailles?|aviculture"
                          r"|aliments?\s+(?:pour\s+)?b[ée]tail|v[ée]t[ée]rinaires?|abattoirs?"
                          r"|p[êe]che|piscicult|halieutiques?|zootechni"),
    (Sector.ALIMENTATION, 1.2, r"vivres?|c[ée]r[ée]ales?|riz\b|ma[ïi]s|mil\b|sorgho|haricots?"
                               r"|huile\s+alimentaire|denr[ée]es?|cantines?\s+scolaires?"
                               r"|restauration|pauses?[\s-]caf[ée]|repas"),
    (Sector.TRANSPORT, 1.2, r"v[ée]hicules?|motos?|motocyclettes?|camions?|bus\b|pi[èe]ces?\s+de\s+rechange"
                            r"|pneumatiques?|entretien\s+de\s+v[ée]hicules?|tricycles?|ambulances?"
                            r"|transport|fret|carburant\s+v[ée]hicule"),
    (Sector.MOBILIER_FOURNITURES, 1.0, r"mobiliers?|fournitures?\s+de\s+bureau|consommables?\s+(?:de\s+)?bureau"
                                       r"|papeterie|chaises?|tables?\b|armoires?|bureaux\b"
                                       r"|climatiseurs?|groupes?\s+froid|r[ée]frig[ée]rateurs?"
                                       r"|gadgets?|imprim[ée]s?"),
    (Sector.COMMUNICATION, 1.2, r"r[ée]gie\s+m[ée]diatique|communication|publicitaires?|affichage"
                                r"|spots?\s+(?:radio|t[ée]l[ée])|[ée]dition|impression\s+de"
                                r"|reportages?|audiovisuel|signal[ée]tique"),
    (Sector.SECURITE, 1.2, r"gardiennage|surveillance|s[ée]curit[ée]|vid[ée]osurveillance"
                           r"|extincteurs?|arm[ée]e|militaires?|d[ée]fense\s+patriotique|\bvdp\b"
                           r"|police|gendarmerie|incendie"),
    (Sector.ENVIRONNEMENT, 1.1, r"reboisement|environnement|d[ée]chets?|ordures?|balayage"
                                r"|nettoyage|plants?\s+forestiers?|p[ée]pini[èe]res?|climat"),
    (Sector.ETUDES_CONSEIL, 1.1, r"[ée]tudes?\s+(?:de\s+faisabilit[ée]|techniques?|architecturales?|d.impact)"
                                 r"|audit|contr[ôo]le\s+(?:technique|des\s+travaux)|suivi[\s-]contr[ôo]le"
                                 r"|ma[îi]trise\s+d.œuvre|consultant|assistance\s+technique"
                                 r"|manifestation\s+d.int[ée]r[êe]t|[ée]laboration\s+(?:du\s+)?(?:plan|sch[ée]ma)"
                                 r"|base\s+de\s+donn[ée]es\s+de\s+fournisseurs|recrutement\s+de\s+(?:fournisseurs|prestataires)"),
    (Sector.FINANCE_ASSURANCE, 1.1, r"assurances?|courtage|cr[ée]dit|micro[\s-]finance|retraite"
                                    r"|comptabilit[ée]|fiscal"),
    (Sector.MINES, 1.1, r"mini[èe]res?|g[ée]ologi|forage\s+mini|carri[èe]res?|granit|lat[ée]rite|sable\b"),
]

# Régions du Burkina Faso telles qu'elles figurent dans les bulletins (nouveau
# découpage). Relevées sur le corpus : Bankui, Djôrô, Guiriko, Kadiogo, Koulsé,
# Liptako, Nakambé, Nazinon, Oubri, Sourou, Tannounyan, Yaadga, Goulmou, Sahel.
# Noms propres de régions, reconnaissables n'importe où dans le texte : ils
# n'existent pas comme mots courants du français.
REGIONS: dict[str, str] = {
    "bankui": "Bankui", "djoro": "Djôrô", "guiriko": "Guiriko", "kadiogo": "Kadiogo",
    "koulse": "Koulsé", "liptako": "Liptako", "nakambe": "Nakambé", "nazinon": "Nazinon",
    "oubri": "Oubri", "sourou": "Sourou", "tannounyan": "Tannounyan", "yaadga": "Yaadga",
    "goulmou": "Goulmou", "sirba": "Sirba", "tapoa": "Tapoa",
    "plateau central": "Plateau central", "hauts bassins": "Hauts-Bassins",
    "boucle du mouhoun": "Boucle du Mouhoun", "sud ouest": "Sud-Ouest",
    "centre est": "Centre-Est", "centre nord": "Centre-Nord",
    "centre ouest": "Centre-Ouest", "centre sud": "Centre-Sud",
}

# Noms de régions qui sont aussi des mots courants. « est » est le verbe être
# avant d'être une région : sans la tournure « Région de l'Est », ces clés
# feraient du mot le plus fréquent du français une région, et 67 annonces du
# corpus se retrouvaient classées « Est ».
AMBIGUOUS_REGIONS: dict[str, str] = {
    "est": "Est", "nord": "Nord", "centre": "Centre", "sahel": "Sahel",
    "cascades": "Cascades",
}

REGION_RE = re.compile(r"r[ée]gion\s+(?:du|de\s+la|de\s+l.|des|de)?\s*([\w'’\- ]{3,30})", re.IGNORECASE)
COMMUNE_RE = re.compile(
    r"(?:commune\s+(?:rurale\s+|urbaine\s+)?de\s+|mairie\s+de\s+|ville\s+de\s+)([\w'’\- ]{2,30})",
    re.IGNORECASE,
)
PROVINCE_RE = re.compile(r"province\s+(?:du|de\s+la|de\s+l.|des|de)?\s*([\w'’\- ]{3,30})", re.IGNORECASE)


class EmitterType(str, Enum):
    """Nature de l'organisme qui publie l'annonce."""

    MINISTERE = "MINISTERE"
    COLLECTIVITE = "COLLECTIVITE"
    ETABLISSEMENT_PUBLIC = "ETABLISSEMENT_PUBLIC"
    SOCIETE_ETAT = "SOCIETE_ETAT"
    PROJET_PROGRAMME = "PROJET_PROGRAMME"
    SANTE_PUBLIQUE = "SANTE_PUBLIQUE"
    ENSEIGNEMENT = "ENSEIGNEMENT"
    AUTRE = "AUTRE"

    @property
    def label(self) -> str:
        return {
            "MINISTERE": "Ministère", "COLLECTIVITE": "Commune ou région",
            "ETABLISSEMENT_PUBLIC": "Agence ou office public",
            "SOCIETE_ETAT": "Société d'État", "PROJET_PROGRAMME": "Projet ou programme",
            "SANTE_PUBLIQUE": "Établissement de santé", "ENSEIGNEMENT": "Établissement d'enseignement",
            "AUTRE": "Autre",
        }[self.value]


_EMITTER_RULES: list[tuple[EmitterType, str]] = [
    (EmitterType.MINISTERE, r"^minist[èe]re|secr[ée]tariat\s+g[ée]n[ée]ral\s+du\s+gouvernement|pr[ée]sidence"),
    (EmitterType.COLLECTIVITE, r"^(?:r[ée]gion|commune|mairie|conseil\s+r[ée]gional|province)\b"),
    (EmitterType.SANTE_PUBLIQUE, r"centre\s+hospitalier|\bchr\b|\bchu\b|chru|\bcma\b|district\s+sanitaire|cameg"),
    (EmitterType.ENSEIGNEMENT, r"universit[ée]|[ée]cole\s+nationale|institut|lyc[ée]e|enafa"),
    (EmitterType.SOCIETE_ETAT, r"soci[ée]t[ée]\s+nationale|sonabhy|sonabel|sonatur|onea|lonab|sonages"),
    (EmitterType.PROJET_PROGRAMME, r"^(?:projet|programme|fonds)\b|\bpata\b|\bfej\b"),
    (EmitterType.ETABLISSEMENT_PUBLIC, r"^(?:agence|office|bureau|caisse|autorit[ée]|centrale\s+d.achat"
                                       r"|direction\s+(?:g[ée]n[ée]rale|r[ée]gionale|provinciale))\b"),
]


@dataclass
class Classification:
    """Classement d'une annonce sur les trois axes de filtrage."""

    sector: Sector
    sector_confidence: float
    sector_evidence: list[str] = field(default_factory=list)
    secondary_sectors: list[str] = field(default_factory=list)
    region: str | None = None
    commune: str | None = None
    province: str | None = None
    emitter_type: EmitterType = EmitterType.AUTRE

    def to_dict(self) -> dict:
        return {
            "sector": self.sector.value,
            "sector_label": self.sector.label,
            "sector_confidence": round(self.sector_confidence, 3),
            "sector_evidence": self.sector_evidence,
            "secondary_sectors": self.secondary_sectors,
            "region": self.region,
            "commune": self.commune,
            "province": self.province,
            "emitter_type": self.emitter_type.value,
            "emitter_label": self.emitter_type.label,
        }


def classify_sector(objet: str, extra: str = "") -> tuple[Sector, float, list[str], list[str]]:
    """Secteur d'un marché, déduit de son objet.

    ``extra`` apporte un complément de contexte (catégorie de la maquette,
    début du corps) utilisé seulement en appui : c'est l'objet qui décide, car
    c'est lui qui dit ce qui est acheté.
    """
    cible = normalize(objet)
    appui = normalize(extra)
    if not cible and not appui:
        return Sector.AUTRE, 0.0, ["objet absent"], []

    # Le début de l'objet nomme ce qui est acheté ; la suite dit le plus souvent
    # où et pour qui. « Électrification solaire de bâtiments publics » est un
    # marché d'énergie, pas de bâtiment : un terme est donc d'autant mieux noté
    # qu'il apparaît tôt.
    scores: dict[Sector, float] = {}
    preuves: dict[Sector, list[str]] = {}
    for secteur, poids, motif in _RULES:
        trouve = re.search(motif, cible)
        if trouve:
            position = trouve.start() / max(len(cible), 1)
            bonus = 1.0 + HEAD_BONUS_MAX * (1.0 - position)
            scores[secteur] = scores.get(secteur, 0.0) + poids * bonus
            ou = "en tête de l'objet" if position < 0.35 else "dans l'objet"
            preuves.setdefault(secteur, []).append(f"« {trouve.group(0)} » {ou}")
            continue
        # Un indice hors de l'objet compte moins : il situe le contexte, il ne
        # dit pas ce qui est acheté.
        trouve = re.search(motif, appui) if appui else None
        if trouve:
            scores[secteur] = scores.get(secteur, 0.0) + poids * 0.35
            preuves.setdefault(secteur, []).append(f"« {trouve.group(0)} » dans le contexte")

    if not scores:
        return Sector.AUTRE, 0.1, ["aucun terme de métier reconnu"], []

    classes = sorted(scores.items(), key=lambda kv: -kv[1])
    secteur, score = classes[0]
    confiance = min(0.97, 0.5 + 0.3 * score)
    # Un second secteur presque aussi bien noté est conservé : un marché de
    # construction d'école relève autant du BTP que de l'éducation, et le
    # lecteur doit le trouver sous les deux.
    secondaires = [s.value for s, v in classes[1:] if v >= score * 0.55][:3]
    return secteur, round(confiance, 3), preuves[secteur][:4], secondaires


def _clean_place(brut: str) -> str:
    """Nettoie un nom de lieu capté par une expression."""
    nom = re.sub(r"\s+", " ", brut).strip(" ,.;:-'’")
    # La capture s'arrête au premier mot de liaison : « Koloko, Province du … ».
    nom = re.split(r"\b(?:pour|au|aux|dans|et|du|de|des|province|r[ée]gion)\b", nom, flags=re.IGNORECASE)[0]
    return nom.strip(" ,.;:-'’").title()


def detect_region(*textes: str) -> tuple[str | None, str | None, str | None]:
    """Région, commune et province mentionnées, dans l'ordre de priorité.

    La région est cherchée d'abord par son nom propre (le découpage de 2025 en
    a changé les intitulés), puis par la tournure « Région du … ».
    """
    # Jointure par saut de ligne : les captures de lieu ne doivent pas
    # enjamber la frontière entre l'émetteur, l'objet et le corps.
    joint = "\n".join(t for t in textes if t)
    normalise = normalize(joint)

    region = None
    for cle, nom in REGIONS.items():
        # Mot entier : « nord » ne doit pas se déclencher sur « nordique ».
        if re.search(rf"\b{re.escape(cle)}\b", normalise):
            region = nom
            break
    if region is None:
        # Les noms ambigus n'ont de valeur que derrière « Région de… ».
        m = REGION_RE.search(joint)
        if m:
            candidat = normalize(m.group(1))
            for cle, nom in AMBIGUOUS_REGIONS.items():
                if re.match(rf"^{re.escape(cle)}\b", candidat):
                    region = nom
                    break
            else:
                region = _clean_place(m.group(1)) or None

    commune = None
    m = COMMUNE_RE.search(joint)
    if m:
        commune = _clean_place(m.group(1)) or None

    province = None
    m = PROVINCE_RE.search(joint)
    if m:
        province = _clean_place(m.group(1)) or None

    return region, commune, province


def classify_emitter(emetteur: str) -> EmitterType:
    texte = normalize(emetteur)
    for type_, motif in _EMITTER_RULES:
        if re.search(motif, texte):
            return type_
    return EmitterType.AUTRE


def classify(
    objet: str,
    emetteur: str = "",
    categorie: str | None = None,
    corps: str = "",
) -> Classification:
    """Classe une annonce sur les trois axes de filtrage."""
    secteur, confiance, preuves, secondaires = classify_sector(
        objet,
        # L'émetteur situe le contexte : une centrale d'achat de médicaments
        # qui achète des palettes reste un marché du secteur de la santé.
        extra="\n".join(filter(None, [emetteur, categorie or "", corps[:800]])),
    )
    region, commune, province = detect_region(emetteur, objet, corps[:1500])
    return Classification(
        sector=secteur, sector_confidence=confiance, sector_evidence=preuves,
        secondary_sectors=secondaires, region=region, commune=commune, province=province,
        emitter_type=classify_emitter(emetteur),
    )
