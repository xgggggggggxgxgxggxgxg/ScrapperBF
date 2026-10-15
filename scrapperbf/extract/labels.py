"""Extraction par motifs — la première marche de la cascade, et la moins chère.

Le corps d'une annonce du Quotidien est plus régulier qu'il n'y paraît :

* un **en-tête étiqueté** (``Financement : …``, ``Montant prévisionnel : …``,
  ``n° : …``) qui se lit sans aucun modèle ;
* des **clauses numérotées** dont les formulations varient, mais autour de
  tournures stables (« doivent parvenir », « délai d'exécution est de »,
  « montant non remboursable de »).

Tout ce qui se lit ici n'a pas besoin du LLM : c'est gratuit, instantané,
reproductible, et chaque valeur sort avec l'extrait de texte qui la justifie —
donc vérifiable par un humain sans ouvrir le PDF.

Les champs portent les noms de ``schema_config.yaml``, pour que le contrôle
qualité et l'extraction LLM parlent du même objet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..core.text import normalize, preview
from .numerals import cross_check_amount

# --- en-tête étiqueté -------------------------------------------------------
# (champ, motif d'étiquette). L'étiquette est ancrée en début de ligne : une
# mention de « financement » au fil d'une phrase n'est pas une déclaration.
LABEL_PATTERNS: list[tuple[str, str]] = [
    ("financement", r"financement"),
    ("montant_previsionnel_global", r"montants?\s+pr[ée]visionnels?(?:\s+(?:global|total))?"),
    ("numero_reference", r"n\s*[°ºo]|r[ée]f[ée]rences?|avis\s+de\s+demande\s+de\s+prix\s*(?:n\s*[°ºo])?"),
    ("autorite_contractante", r"(?:identifiant\s+de\s+l.)?autorit[ée]\s+contractante"),
    ("maitre_ouvrage_delegue", r"ma[îi]tre\s+d.ouvrage\s+d[ée]l[ée]gu[ée]"),
    ("maitre_ouvrage", r"ma[îi]tre\s+d.ouvrage"),
    ("reference_step", r"r[ée]f[ée]rence\s+step"),
]

# --- clauses du corps -------------------------------------------------------
# (champ, motif). Le motif capture la valeur dans son groupe 1 quand il en a un,
# sinon la phrase entière sert de valeur : pour une condition d'éligibilité,
# c'est la phrase qui porte l'information, pas un fragment.
#
# `[^.]` dans un motif traverse les fins de ligne, ce qui est voulu pour une
# phrase qui se poursuit, mais désastreux à côté d'un tableau : la capture
# aspire les cellules (« réservée aux 1,00 4 »). Les motifs dont la valeur
# tient sur une ligne utilisent donc `[^.\n]`.
CLAUSE_PATTERNS: list[tuple[str, str]] = [
    # L'échéance elle-même, la forme la plus précise et la plus fréquente :
    # « …au plus tard le 12/10/2026 à 09 heures 00 TU ».
    ("date_limite_depot", r"(?:au\s+plus\s+tard|avant)\s+le\s+([^.\n]{6,140})"),
    ("date_limite_depot", r"date\s+limite\s+(?:de\s+)?(?:d[ée]p[ôo]t|r[ée]ception)[^.\n]{0,160}"),
    # À défaut, la phrase d'acheminement. On n'exige plus la proximité du mot
    # « offres » : la clause de garantie s'intercale souvent sur 200 caractères,
    # alors que « devront parvenir ou être remises » suffit à lever le doute.
    ("date_limite_depot",
     r"(?:devront?|doivent|seront|sera)\s+(?:parvenir|[êe]tre\s+remises?|[êe]tre\s+soumises?"
     r"|d[ée]pos[ée]e?s?|re[çc]ue?s?)([^.]{0,240})"),
    ("date_ouverture_plis",
     r"(?:ouverture\s+des\s+plis|d[ée]pouillement)[^.]{0,200}"),
    ("delai_execution",
     r"d[ée]lai\s+(?:d.ex[ée]cution|de\s+livraison|de\s+r[ée]alisation)[^.\n]{0,160}"),
    ("delai_validite_offres",
     r"(?:rester|resteront|demeurent|demeureront)\s+engag[ée]s?[^.]{0,160}"),
    ("delai_validite_offres", r"d[ée]lai\s+de\s+validit[ée]\s+des\s+offres[^.]{0,120}"),
    ("garantie_soumission",
     r"(?:garantie|caution(?:nement)?)\s+(?:de\s+)?soumission[^.]{0,200}"),
    ("acquisition_dossier",
     r"montant\s+non\s+remboursable[^.]{0,200}"),
    ("conditions_eligibilite_generales",
     r"participation\s+[àa]\s+la\s+concurrence\s+est\s+ouverte[^.]{0,400}"),
    ("renseignements_et_contact",
     r"(?:t[ée]l[ée]phone|t[ée]l)\s*\.?\s*:?[^.]{0,140}"),
    ("visite_site", r"(?:la\s+)?visite\s+(?:des\s+lieux|de\s+site|du\s+site)[^.\n]{0,120}"),
    # On exige du contenu après « aux » : dans certaines annonces la liste des
    # entreprises réservées est dans un tableau, et « réservée aux » tout seul
    # n'est pas une information. L'absence est alors plus honnête.
    ("reservation", r"(?:est\s+)?r[ée]serv[ée]e?\s+aux\s+([^.\n]*[A-Za-zÀ-ÿ]{3}[^.\n]{0,150})"),
    ("reservation", r"[àa]\s+direction\s+f[ée]minine[^.\n]{0,80}"),
    ("rectificatif_de", r"rectificatif[^.\n]{0,200}"),
    ("type_avis",
     r"avis\s+(?:de\s+demande\s+de\s+prix(?:\s+[àa]\s+commandes)?|d.appel\s+d.offres"
     r"(?:\s+(?:ouvert|acc[ée]l[ée]r[ée])(?:\s+acc[ée]l[ée]r[ée])?)?"
     r"|[àa]\s+manifestation\s+d.int[ée]r[êe]t|de\s+sollicitation\s+de\s+prix"
     r"|de\s+recrutement|d.annulation)|demande\s+de\s+propositions"),
]

# Une référence occupe parfois sa propre ligne, sans étiquette ni deux-points :
#     Avis à manifestation d'intérêt
#     N°2026-03/RGRK/PTUY/CKBIA
BARE_REFERENCE_RE = re.compile(
    r"^\s*(n\s*[°ºo]\s*\d{2,4}\s*[-/][\w\-/.° ]{4,90})\s*$", re.IGNORECASE | re.MULTILINE
)

LOT_RE = re.compile(
    r"^\s*lot\s*(?:n\s*[°ºo]\s*)?(\d{1,2})\s*[:\-–]\s*(.{3,200})$", re.IGNORECASE | re.MULTILINE
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
# Un numéro burkinabè a huit chiffres, mais huit chiffres ne font pas un
# numéro : « BMZ N° 2022 67 490 » et « N°2026-00010 » en contiennent aussi.
# On n'accepte donc que ce qui suit une mention de contact, et on refuse toute
# suite collée à d'autres chiffres.
PHONE_CONTEXT_RE = re.compile(
    r"(?:t[ée]l[ée]phones?|t[ée]l|cel(?:lulaire)?|mobile|portable|contacts?|appeler)"
    r"\s*\.?\s*:?\s*((?:\(?00226\)?)?[\s\d.\-/]{8,60})",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?<!\d)(?:\(?00226\)?\s*)?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)")
# Un montant est significatif à partir de quatre chiffres : « 03 mois » n'en est pas un.
# Le montant en chiffres est écrit entre parenthèses, juste après sa version
# en lettres : « … quarante un (21 186 441) FCFA HTVA ». La parenthèse
# fermante sépare donc les chiffres de la devise, et doit être tolérée.
AMOUNT_RE = re.compile(
    r"\d[\d\s.  ]{3,}\d\s*\)?\s*(?:FCFA|F\s*CFA|XOF|francs?)", re.IGNORECASE
)
HTVA_RE = re.compile(
    r"([\d\s.  ]{4,})\s*\)?\s*(?:FCFA|F\s*CFA)?\s*HT(?:VA)?", re.IGNORECASE
)
TTC_RE = re.compile(
    r"([\d\s.  ]{4,})\s*\)?\s*(?:FCFA|F\s*CFA)?\s*TTC", re.IGNORECASE
)


@dataclass
class Extracted:
    """Une valeur extraite, avec sa provenance et sa justification."""

    field: str
    value: object
    source: str                      # "etiquette" | "clause" | "maquette" | "llm"
    evidence: str = ""               # extrait du texte qui porte la valeur
    confidence: float = 0.9
    cross_check: bool | None = None  # concordance lettres / chiffres

    def to_dict(self) -> dict:
        return {
            "field": self.field,
            "value": self.value,
            "source": self.source,
            "evidence": self.evidence,
            "confidence": round(self.confidence, 3),
            "cross_check": self.cross_check,
        }


@dataclass
class RegexExtraction:
    """Résultat de l'extraction par motifs."""

    fields: dict[str, Extracted] = field(default_factory=dict)
    lots: list[dict] = field(default_factory=list)

    def as_record(self) -> dict:
        """Dict au format attendu par le contrôle qualité et le schéma."""
        record = {name: e.value for name, e in self.fields.items()}
        if self.lots:
            record["lots"] = self.lots
            record["est_en_lots"] = len(self.lots) > 1
        return record

    def to_dict(self) -> dict:
        return {
            "fields": {n: e.to_dict() for n, e in self.fields.items()},
            "lots": self.lots,
        }


def _label_regex(pattern: str) -> re.Pattern[str]:
    """Motif d'étiquette ancré en début de ligne.

    Le motif reçu ne doit contenir que des groupes non capturants : la valeur
    est le groupe 1, et un groupe capturant dans l'étiquette décalerait cette
    numérotation. L'assertion garde cette contrainte visible plutôt que de la
    laisser échouer plus loin.
    """
    sonde = re.compile(pattern)
    assert sonde.groups == 0, f"l'étiquette « {pattern} » ne doit pas capturer"
    # L'étiquette est encadrée par un groupe non capturant : sans lui, une
    # alternation « a|b|c » dans le motif se lierait à toute l'expression, et
    # le groupe de valeur n'existerait que dans la dernière branche.
    return re.compile(rf"^\s*(?:{pattern})\s*:\s*(\S.*)$", re.IGNORECASE | re.MULTILINE)


def extract_labels(text: str) -> dict[str, Extracted]:
    """Lit les paires ``Étiquette : valeur`` de l'en-tête."""
    out: dict[str, Extracted] = {}
    for name, pattern in LABEL_PATTERNS:
        if name in out:
            continue  # la première étiquette rencontrée fait foi
        m = _label_regex(pattern).search(text)
        if not m:
            continue
        valeur = m.group(1).strip().rstrip(".,;")
        out[name] = Extracted(
            field=name, value=valeur, source="etiquette",
            evidence=preview(m.group(0), 160),
            # Une étiquette explicite est la source la plus sûre du corps.
            confidence=0.97,
        )
    return out


def extract_bare_reference(text: str) -> dict[str, Extracted]:
    """Référence posée seule sur sa ligne, sans étiquette."""
    m = BARE_REFERENCE_RE.search(text)
    if not m:
        return {}
    return {
        "numero_reference": Extracted(
            field="numero_reference", value=m.group(1).strip(), source="clause",
            evidence=preview(m.group(0), 120),
            # Moins sûr qu'une étiquette explicite, plus sûr qu'une tournure.
            confidence=0.9,
        )
    }


def extract_clauses(text: str) -> dict[str, Extracted]:
    """Lit les valeurs portées par les clauses du corps."""
    out: dict[str, Extracted] = {}
    for name, pattern in CLAUSE_PATTERNS:
        if name in out:
            continue
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        brut = (m.group(1) if m.groups() and m.group(1) else m.group(0)).strip()
        brut = re.sub(r"\s+", " ", brut).rstrip(".,;: ")
        # Une capture quasi vide de contenu n'est pas une valeur : c'est un
        # reste de tableau ou une amorce de phrase coupée. On compte les
        # caractères alphanumériques, et non les mots — « 12/10/2026 à » est
        # une échéance parfaitement valable, sans aucun mot de trois lettres.
        if sum(c.isalnum() for c in brut) < 3:
            continue
        out[name] = Extracted(
            field=name, value=brut, source="clause",
            evidence=preview(m.group(0), 200),
            # Une clause est plus incertaine qu'une étiquette : la formulation
            # varie, et la phrase capturée peut déborder sur la suivante.
            confidence=0.8,
        )
    return out


def extract_lots(text: str) -> list[dict]:
    """Lit la liste des lots, quand le marché est alloti."""
    lots = []
    vus = set()
    for m in LOT_RE.finditer(text):
        numero = int(m.group(1))
        if numero in vus:
            continue
        vus.add(numero)
        designation = re.sub(r"\s+", " ", m.group(2)).strip()
        lots.append({"numero": numero, "designation": designation[:300]})
    return sorted(lots, key=lambda l: l["numero"])


def extract_amounts(text: str) -> dict[str, Extracted]:
    """Montant prévisionnel : HTVA, TTC, et recoupement lettres / chiffres."""
    out: dict[str, Extracted] = {}
    # L'étiquette d'abord ; à défaut la tournure de clause, qui porte la même
    # information un peu plus loin dans le corps.
    for motif in (r"montants?\s+pr[ée]visionnels?[^.]{0,320}",
                  r"(?:budget|enveloppe|co[ûu]t)\s+pr[ée]visionnel[^.]{0,320}"):
        m = re.search(motif, text, re.IGNORECASE)
        if m:
            break
    if not m:
        return out
    segment = m.group(0)

    htva = HTVA_RE.search(segment)
    ttc = TTC_RE.search(segment)
    valeur = {}
    if htva:
        valeur["htva"] = htva.group(1).strip()
    if ttc:
        valeur["ttc"] = ttc.group(1).strip()
    if not valeur:
        amount = AMOUNT_RE.search(segment)
        if amount:
            valeur["montant"] = amount.group(0).strip()
    if not valeur:
        return out

    lettres, chiffres, concordent = cross_check_amount(segment)
    out["montant_previsionnel_global"] = Extracted(
        field="montant_previsionnel_global", value=valeur, source="etiquette",
        evidence=preview(segment, 220),
        # Lettres et chiffres concordants : c'est la valeur la plus sûre que le
        # document puisse offrir. Discordants : on la sort quand même, mais
        # signalée, car c'est à un humain de trancher.
        confidence=0.99 if concordent else (0.6 if concordent is False else 0.9),
        cross_check=concordent,
    )
    return out


def extract_contacts(text: str) -> dict[str, Extracted]:
    """Téléphones et courriels, regroupés en point de contact."""
    telephones: list[str] = []
    vus: set[str] = set()   # formes normalisées, pour dédoublonner
    # On ne cherche les numéros que dans les segments annoncés comme contacts.
    segments = " ; ".join(m.group(1) for m in PHONE_CONTEXT_RE.finditer(text))
    for m in PHONE_RE.finditer(segments):
        brut = re.sub(r"\s+", " ", m.group(0)).strip()
        chiffres = re.sub(r"\D", "", brut)
        chiffres = chiffres[5:] if chiffres.startswith("00226") else chiffres
        # Comparer la forme normalisée à la forme normalisée : le même numéro
        # est écrit tantôt avec, tantôt sans son indicatif.
        if len(chiffres) == 8 and chiffres not in vus:
            vus.add(chiffres)
            telephones.append(brut)
    courriels = sorted(set(EMAIL_RE.findall(text)))
    if not telephones and not courriels:
        return {}
    valeur: dict[str, object] = {}
    if telephones:
        valeur["telephones"] = telephones[:6]
    if courriels:
        valeur["courriels"] = courriels[:4]
    return {
        "renseignements_et_contact": Extracted(
            field="renseignements_et_contact", value=valeur, source="clause",
            evidence=preview(" ".join(telephones + courriels), 160), confidence=0.9,
        )
    }


def extract_all(text: str, header: dict[str, object] | None = None) -> RegexExtraction:
    """Extraction complète par motifs d'une annonce.

    ``header`` apporte ce que la maquette a déjà établi (émetteur, catégorie,
    objet). Ces valeurs sont **prioritaires** : elles viennent de la couleur
    des bandes, pas d'une tournure de phrase.
    """
    resultat = RegexExtraction()
    resultat.fields.update(extract_clauses(text))
    resultat.fields.update(extract_bare_reference(text))
    resultat.fields.update(extract_labels(text))      # l'étiquette bat la clause
    resultat.fields.update(extract_amounts(text))
    contacts = extract_contacts(text)
    for name, extracted in contacts.items():
        # On ne remplace une valeur de clause que si la nôtre est structurée.
        if name not in resultat.fields or isinstance(extracted.value, dict):
            resultat.fields[name] = extracted
    resultat.lots = extract_lots(text)

    for champ, valeur in (header or {}).items():
        if valeur:
            resultat.fields[champ] = Extracted(
                field=champ, value=valeur, source="maquette",
                evidence="bande colorée de la maquette", confidence=1.0,
            )
    return resultat
