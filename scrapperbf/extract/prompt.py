"""Construction du prompt d'extraction, à partir de ``extraction_rules.yaml``.

Deux idées portent ce module.

**Les consignes vivent dans le YAML, pas dans le code.** Changer ce que le
modèle comprend ne demande pas de toucher à Python, et le prompt n'embarque que
les champs demandés : interroger le modèle sur douze champs quand deux manquent
coûte douze fois plus cher pour le même résultat.

**On fait choisir, pas chercher.** C'est la réponse au problème des valeurs
multiples : une annonce contient cinq dates et trois suites de huit chiffres, et
demander « la date limite de dépôt » laisse le modèle arbitrer seul, sans qu'on
sache ensuite sur quoi il s'est fondé. On relève donc d'abord *tous* les
candidats par expression régulière, on les présente numérotés avec leur
contexte, et on demande au modèle de désigner celui qui convient et de citer le
texte qui le justifie. Le modèle fait ce qu'il fait bien — comprendre une
tournure — et nous gardons de quoi le vérifier.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from ..core.text import preview

RULES_PATH = Path(__file__).resolve().parents[2] / "extraction_rules.yaml"

# Champs pour lesquels une liste de candidats est utile : ceux dont le corpus
# porte plusieurs valeurs de même forme, donc confondables.
CANDIDATE_KINDS: dict[str, str] = {
    "date_limite_depot": "dates",
    "date_ouverture_plis": "dates",
    "montant_previsionnel_global": "montants",
    "garantie_soumission": "montants",
    "acquisition_dossier": "montants",
    "renseignements_et_contact": "telephones",
    "numero_reference": "references",
    "delai_execution": "durees",
    "delai_validite_offres": "durees",
}

DATE_CANDIDATE_RE = re.compile(
    r"\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}"
    r"|\d{1,2}\s*(?:er)?\s+(?:janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t"
    r"|septembre|octobre|novembre|d[ée]cembre)\s+\d{4}",
    re.IGNORECASE,
)
AMOUNT_CANDIDATE_RE = re.compile(
    r"\d[\d\s.  ]{2,}\d\s*\)?\s*(?:FCFA|F\s*CFA|XOF|francs?)", re.IGNORECASE
)
PHONE_CANDIDATE_RE = re.compile(
    r"(?<!\d)(?:\(?\+?00?226\)?\s*)?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}[\s.\-]?\d{2}(?!\d)"
)
# Une référence exige des chiffres et un séparateur. Accepter la lettre « o »
# comme substitut du signe degré faisait de « n|on remboursable » une
# référence : le motif doit être ancré sur le numéro, pas sur la lettre.
REFERENCE_CANDIDATE_RE = re.compile(
    # Le deux-points de l'étiquette « n° : 2026-23/… » doit être toléré.
    r"\bn\s*[°º]?\s*:?\s*\d{2,4}\s*[-/][\w\-/.\s]{2,50}", re.IGNORECASE
)
DURATION_CANDIDATE_RE = re.compile(
    r"\d+\s*\)?\s*(?:jours?|mois|semaines?|ans?|ann[ée]es?)", re.IGNORECASE
)
CONTEXT_CHARS = 90


@dataclass
class Candidate:
    """Une valeur possible, avec son contexte, offerte au choix du modèle."""

    index: int
    kind: str
    value: str
    context: str

    def to_line(self) -> str:
        return f"  [{self.kind[:3]}{self.index}] « {self.value} »  —  contexte : …{self.context}…"


@dataclass
class PromptSpec:
    """Prompt prêt à envoyer, et ce qui a servi à le composer."""

    system: str
    user: str
    fields: list[str]
    candidates: dict[str, list[Candidate]] = field(default_factory=dict)
    schema: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.system) + len(self.user)


@lru_cache(maxsize=None)
def load_rules(path: str | None = None) -> dict:
    target = Path(path) if path else RULES_PATH
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


def known_fields(path: str | None = None) -> list[str]:
    return list((load_rules(path).get("champs") or {}).keys())


def collect_candidates(text: str) -> dict[str, list[Candidate]]:
    """Relève toutes les valeurs de forme ambiguë présentes dans le texte."""
    patterns = {
        "dates": DATE_CANDIDATE_RE,
        "montants": AMOUNT_CANDIDATE_RE,
        "telephones": PHONE_CANDIDATE_RE,
        "references": REFERENCE_CANDIDATE_RE,
        "durees": DURATION_CANDIDATE_RE,
    }
    out: dict[str, list[Candidate]] = {}
    for kind, pattern in patterns.items():
        vus: set[str] = set()
        trouves: list[Candidate] = []
        for m in pattern.finditer(text):
            valeur = re.sub(r"\s+", " ", m.group(0)).strip()
            cle = re.sub(r"\W", "", valeur).lower()
            if not cle or cle in vus:
                continue
            vus.add(cle)
            debut = max(0, m.start() - CONTEXT_CHARS)
            fin = min(len(text), m.end() + CONTEXT_CHARS)
            trouves.append(Candidate(
                index=len(trouves) + 1, kind=kind, value=valeur,
                context=preview(text[debut:fin], 2 * CONTEXT_CHARS),
            ))
        if trouves:
            out[kind] = trouves
    return out


def _render_field(name: str, spec: dict) -> str:
    """Rend la consigne d'un champ, telle qu'elle apparaît dans le prompt."""
    lignes = [f"### {name} — {spec.get('intitule', name)}"]
    if spec.get("definition"):
        lignes.append(f"Ce que c'est : {spec['definition']}")
    if spec.get("ou_chercher"):
        lignes.append("Où le chercher :")
        lignes += [f"  - {o}" for o in spec["ou_chercher"]]

    desambig = spec.get("desambiguisation") or {}
    if desambig.get("regle"):
        lignes.append(f"Comment choisir : {desambig['regle']}")
    if desambig.get("si_plusieurs"):
        lignes.append(f"Si plusieurs valeurs conviennent : {desambig['si_plusieurs']}")
    for confusion in spec.get("confusions") or []:
        lignes.append(
            f"Ne pas confondre avec « {confusion.get('champ')} » : {confusion.get('distinguer')}"
        )
    if spec.get("format"):
        lignes.append(f"Forme attendue : {spec['format']}")
    for exemple in spec.get("exemples") or []:
        valeur = exemple.get("valeur")
        rendu = json.dumps(valeur, ensure_ascii=False) if isinstance(valeur, (dict, list)) else f'"{valeur}"'
        lignes.append(f"Exemple — texte : « {exemple.get('texte', '')[:220]} »\n  → valeur : {rendu}")
    for doute in spec.get("doute") or []:
        lignes.append(f"Baisser la confiance si : {doute}")
    if "si_absent" in spec:
        lignes.append(f"Si l'information est absente : rendre {json.dumps(spec['si_absent'])}")
    return "\n".join(lignes)


def build_schema(fields: list[str]) -> dict:
    """Schéma de réponse : une enveloppe par champ, pour pouvoir juger après.

    Chaque champ rend sa valeur, mais aussi la confiance du modèle, la citation
    qui la porte et les candidats qu'il a écartés. Sans ces trois éléments,
    aucune vérification automatique n'est possible — on ne pourrait que croire.
    """
    return {
        "type": "object",
        "properties": {
            name: {
                "type": "object",
                "properties": {
                    "valeur": {"description": "la valeur extraite, ou null si absente"},
                    "confiance": {
                        "type": "number",
                        "description": "0 à 1 : à quel point tu es sûr de cette valeur",
                    },
                    "citation": {
                        "type": "string",
                        "description": "extrait du texte, copié littéralement, qui porte la valeur",
                    },
                    "candidats_ecartes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "valeurs plausibles que tu as écartées, et pourquoi",
                    },
                },
                "required": ["valeur", "confiance", "citation"],
            }
            for name in fields
        },
        "required": list(fields),
    }


def build_prompt(
    body: str,
    fields: list[str] | None = None,
    *,
    header: dict[str, object] | None = None,
    rules_path: str | None = None,
    with_candidates: bool = True,
    bulletin_date: str | None = None,
) -> PromptSpec:
    """Compose le prompt pour les champs demandés.

    ``header`` apporte ce que la maquette du PDF a déjà établi de façon sûre
    (émetteur, objet, catégorie). On le donne au modèle comme un acquis, pas
    comme une question : il sert d'ancrage et évite qu'il le cherche à nouveau
    dans le texte, où il risquerait de trouver autre chose.
    """
    rules = load_rules(rules_path)
    specs = rules.get("champs") or {}
    demandes = [f for f in (fields or specs.keys()) if f in specs]
    inconnus = [f for f in (fields or []) if f not in specs]

    system = "\n".join(
        [
            *(rules.get("generales") or []),
            "",
            "Tu réponds UNIQUEMENT par un objet JSON conforme au schéma fourni, "
            "sans texte avant ni après.",
        ]
    )

    parties: list[str] = []
    if header:
        connus = "\n".join(f"  - {k} : {v}" for k, v in header.items() if v)
        if connus:
            parties.append(
                "INFORMATIONS DÉJÀ ÉTABLIES avec certitude par la mise en page du PDF.\n"
                "Reprends-les telles quelles, ne les cherche pas dans le texte :\n" + connus
            )
    if bulletin_date:
        parties.append(
            f"Date de parution du bulletin : {bulletin_date}. "
            "Toute échéance de dépôt lui est postérieure."
        )

    candidats: dict[str, list[Candidate]] = {}
    if with_candidates:
        releves = collect_candidates(body)
        besoins = {CANDIDATE_KINDS[f] for f in demandes if f in CANDIDATE_KINDS}
        candidats = {k: v for k, v in releves.items() if k in besoins}
        if candidats:
            bloc = [
                "VALEURS CANDIDATES relevées mécaniquement dans le texte. La bonne "
                "réponse est presque toujours l'une d'elles : choisis, ne cherche pas "
                "ailleurs. Si aucune ne convient, tu peux répondre autrement en le "
                "justifiant dans la citation.",
            ]
            for kind, liste in candidats.items():
                bloc.append(f"\n{kind.upper()} ({len(liste)} trouvées) :")
                bloc += [c.to_line() for c in liste]
            parties.append("\n".join(bloc))

    parties.append(
        "CHAMPS À EXTRAIRE — les consignes ci-dessous sont à appliquer champ par champ.\n\n"
        + "\n\n".join(_render_field(name, specs[name]) for name in demandes)
    )
    if inconnus:
        parties.append(
            "Champs demandés mais absents du référentiel, à ignorer : " + ", ".join(inconnus)
        )
    parties.append("TEXTE DE L'ANNONCE :\n" + body)

    return PromptSpec(
        system=system,
        user="\n\n".join(parties),
        fields=demandes,
        candidates=candidats,
        schema=build_schema(demandes),
    )
