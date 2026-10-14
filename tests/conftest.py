"""Fixtures communes : un bulletin de test qui reproduit les pièges réels.

Le Markdown ci-dessous imite la structure du Quotidien DGCMEF et contient
volontairement les défauts que la plateforme doit détecter : une annonce qui
déborde sur deux pages, une page dupliquée, un trou de numérotation, un titre
non classable, et une annonce tronquée.
"""

import pytest

BULLETIN = """# Sommaire
Table des matieres du present numero.

**1**

# MINISTERES ET INSTITUTIONS

## Fournitures et Services courants

### MINISTERE DE L'ECONOMIE, DES FINANCES ET DE LA PROSPECTIVE

## Avis de demande de prix N°2026-012/MEF/SG/DMP

1. Le Ministere de l'Economie lance une demande de prix pour l'acquisition de materiel
informatique au profit de la Direction des Marches Publics.
2. Le financement est assure par le budget de l'Etat, gestion 2026.
3. Le montant previsionnel est de 25 000 000 F CFA TTC.
4. Les offres sont recues au plus tard le 14 aout 2026 a 09 heures 00 au secretariat de la DMP.
5. L'ouverture des plis aura lieu le 14 aout 2026 a 09 heures 30 en presence des soumissionnaires.
6. Les soumissionnaires resteront engages pendant quatre-vingt-dix (90) jours.

Le Directeur General des Marches Publics.

**12**

## Travaux

### COMMUNE DE OUAGADOUGOU

## Travaux de construction d'un batiment administratif au secteur 30 de Ouagadougou

1. La Commune de Ouagadougou lance un appel d'offres ouvert pour les travaux de construction
d'un batiment administratif de type R+1 au secteur 30.
2. Le montant previsionnel des travaux est de 450 000 000 F CFA HTVA.
3. Le delai d'execution est de huit (08) mois a compter de la notification de l'ordre de service
4. Les offres doivent parvenir au secretariat de la mairie au plus tard le 20 aout 2026 a 10 heures 00,
le dossier etant retire contre paiement de la somme de 150 000 F CFA a la recette municipale de

**13**

5. Les soumissionnaires resteront engages pendant cent vingt (120) jours a compter de la date limite.
6. Les plis seront ouverts en seance publique dans la salle de reunion de la mairie.

Le Maire de la Commune de Ouagadougou.

**14**

# COLLECTIVITES TERRITORIALES

## RESULTATS PROVISOIRES

### REGION DU CENTRE-NORD

Synthese des resultats de la demande de prix N°2026-003/RCNR/GVRT pour l'entretien des
voiries urbaines de la commune de Kaya, delibere le 02 juillet 2026, trois plis recus.

| Soumissionnaire | Montant lu HTVA | Montant corrige | Observations |
| ENTREPRISE ALPHA | 18 500 000 | 18 500 000 | ATTRIBUTAIRE pour un delai de 45 jours |

**16**

## Communique sans intitule reconnaissable par le catalogue de natures

Le present communique informe les operateurs economiques de la tenue d'une seance
d'information relative aux procedures de passation des marches publics au titre de l'annee.

**17**
"""

# Page strictement identique à la page 12, pour éprouver la détection de doubles.
_PAGE_12 = BULLETIN.split("**12**")[0].split("# MINISTERES ET INSTITUTIONS")[1]
BULLETIN_AVEC_DOUBLON = BULLETIN + "\n# MINISTERES ET INSTITUTIONS" + _PAGE_12 + "\n**12**\n"


@pytest.fixture
def bulletin() -> str:
    return BULLETIN


@pytest.fixture
def bulletin_doublon() -> str:
    return BULLETIN_AVEC_DOUBLON


@pytest.fixture
def extraction_demande_prix() -> dict:
    """Extraction correcte et complète de l'avis de la page 12."""
    return {
        "autorite_emettrice": "Ministere de l'Economie, des Finances et de la Prospective",
        "objet": "Acquisition de materiel informatique au profit de la Direction des Marches Publics",
        "numero_reference": "N°2026-012/MEF/SG/DMP",
        "type_avis": "Avis de demande de prix",
        "financement": "Budget de l'Etat, gestion 2026",
        "montant_previsionnel_global": {"ttc": "25 000 000 F CFA"},
        "date_limite_depot": "14 aout 2026 a 09 heures 00",
        "date_ouverture_plis": "14 aout 2026 a 09 heures 30",
        "delai_validite_offres": "90 jours",
        "acquisition_dossier": {"lieu": "Direction des Marches Publics"},
        "est_en_lots": False,
    }


@pytest.fixture
def store(tmp_path):
    from scrapperbf.feedback.store import FeedbackStore

    return FeedbackStore(tmp_path / "feedback.db")
