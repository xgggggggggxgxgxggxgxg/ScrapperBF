"""Priorité des champs, dérivée de la nature du titre.

Toutes les informations d'une annonce n'ont pas la même valeur. Pour un avis de
demande de prix, une date limite de dépôt absente rend l'annonce inutilisable ;
un horaire de retrait de dossier manquant est un détail. Le scoring QA doit
donc pondérer, pas compter.

La source de vérité reste ``schema_config.yaml`` (``required: true`` →
CRITIQUE). Ce module ajoute la couche qui dépend de la *nature* de l'annonce,
que le YAML ne peut pas exprimer : les champs qu'un avis de manifestation
d'intérêt doit porter ne sont pas ceux d'un avis d'attribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from pathlib import Path

import yaml

from .titles import SECTION_OF_KIND, TitleKind

DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema_config.yaml"


class Priority(IntEnum):
    """Poids d'un champ dans le score de complétude."""

    OPTIONAL = 1
    IMPORTANT = 2
    CRITICAL = 3

    @property
    def label(self) -> str:
        return {1: "optionnel", 2: "important", 3: "critique"}[int(self)]


# Champs promus CRITIQUE ou IMPORTANT selon la nature de l'annonce. Un champ
# non cité garde la priorité déduite du YAML (CRITICAL si `required`, sinon
# OPTIONAL) ; un champ promu ne peut jamais être rétrogradé.
_PROMOTIONS: dict[TitleKind, dict[str, Priority]] = {
    TitleKind.DEMANDE_PRIX: {
        "date_limite_depot": Priority.CRITICAL,
        "numero_reference": Priority.CRITICAL,
        "montant_previsionnel_global": Priority.IMPORTANT,
        "financement": Priority.IMPORTANT,
        "date_ouverture_plis": Priority.IMPORTANT,
        "acquisition_dossier": Priority.IMPORTANT,
        "delai_validite_offres": Priority.IMPORTANT,
    },
    TitleKind.APPEL_OFFRES: {
        "date_limite_depot": Priority.CRITICAL,
        "numero_reference": Priority.CRITICAL,
        "conditions_eligibilite_generales": Priority.IMPORTANT,
        "financement": Priority.IMPORTANT,
        "date_ouverture_plis": Priority.IMPORTANT,
        "acquisition_dossier": Priority.IMPORTANT,
        "est_en_lots": Priority.IMPORTANT,
    },
    TitleKind.MANIFESTATION_INTERET: {
        "date_limite_depot": Priority.CRITICAL,
        "numero_reference": Priority.IMPORTANT,
        "conditions_eligibilite_generales": Priority.IMPORTANT,
        "renseignements_et_contact": Priority.IMPORTANT,
        # Une AMI ne chiffre presque jamais un montant prévisionnel : l'exiger
        # produirait du bruit d'alerte sur des annonces pourtant complètes.
        "montant_previsionnel_global": Priority.OPTIONAL,
    },
    TitleKind.DEMANDE_PROPOSITIONS: {
        "date_limite_depot": Priority.CRITICAL,
        "numero_reference": Priority.IMPORTANT,
        "conditions_eligibilite_generales": Priority.IMPORTANT,
        "delai_validite_offres": Priority.IMPORTANT,
    },
    TitleKind.RECTIFICATIF: {
        # Un rectificatif ne vaut que s'il désigne l'avis qu'il corrige.
        "numero_reference": Priority.CRITICAL,
        "objet": Priority.CRITICAL,
        "date_limite_depot": Priority.IMPORTANT,
    },
    TitleKind.ANNULATION: {
        "numero_reference": Priority.CRITICAL,
        "objet": Priority.CRITICAL,
    },
    TitleKind.RESULTATS: {
        "attributaire_provisoire": Priority.CRITICAL,
        "montant_attribue_min": Priority.IMPORTANT,
        "nombre_plis_recus": Priority.IMPORTANT,
        "date_deliberation": Priority.IMPORTANT,
        "soumissionnaires": Priority.IMPORTANT,
        "type_procedure": Priority.IMPORTANT,
    },
    TitleKind.AVIS_ATTRIBUTION: {
        "attributaire_provisoire": Priority.CRITICAL,
        "montant_attribue_min": Priority.IMPORTANT,
    },
}


@dataclass(frozen=True)
class FieldProfile:
    """Jeu de champs attendus pour une nature d'annonce, avec leurs priorités."""

    kind: TitleKind
    section: str
    priorities: dict[str, Priority]

    def priority(self, field: str) -> Priority:
        return self.priorities.get(field, Priority.OPTIONAL)

    def fields_at(self, level: Priority) -> list[str]:
        return sorted(f for f, p in self.priorities.items() if p == level)

    @property
    def critical_fields(self) -> list[str]:
        return self.fields_at(Priority.CRITICAL)

    @property
    def total_weight(self) -> int:
        return sum(int(p) for p in self.priorities.values())


@lru_cache(maxsize=None)
def load_schema(path: str | None = None) -> dict:
    target = Path(path) if path else DEFAULT_SCHEMA_PATH
    return yaml.safe_load(target.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=None)
def profile_for(kind: TitleKind, schema_path: str | None = None) -> FieldProfile:
    """Construit le profil de champs d'une nature d'annonce.

    Les natures non porteuses d'annonce (marqueurs, inconnu) reçoivent le
    profil AVIS par défaut : mieux vaut évaluer une annonce mal classée que de
    la sortir silencieusement du contrôle qualité.
    """
    section = SECTION_OF_KIND.get(kind, "AVIS")
    schema = load_schema(schema_path)
    fields = schema.get(section, {}) or {}

    priorities: dict[str, Priority] = {}
    for name, spec in fields.items():
        spec = spec or {}
        priorities[name] = Priority.CRITICAL if spec.get("required") else Priority.OPTIONAL

    for name, promoted in _PROMOTIONS.get(kind, {}).items():
        # Un champ promu pour cette nature mais absent du YAML est ignoré :
        # le schéma reste l'unique source des champs existants.
        if name in priorities:
            priorities[name] = max(priorities[name], promoted)

    return FieldProfile(kind=kind, section=section, priorities=priorities)


def priority_table() -> str:
    """Vue lisible du profil de chaque nature — utile en revue de config."""
    lines = []
    for kind in SECTION_OF_KIND:
        prof = profile_for(kind)
        lines.append(f"{kind.value} ({prof.section}) poids total {prof.total_weight}")
        for level in (Priority.CRITICAL, Priority.IMPORTANT):
            names = prof.fields_at(level)
            if names:
                lines.append(f"  {level.label:>9} : {', '.join(names)}")
    return "\n".join(lines)
