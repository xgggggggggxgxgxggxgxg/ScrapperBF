from scrapperbf.core.priority import Priority, profile_for
from scrapperbf.core.titles import TitleKind


def test_les_champs_required_du_yaml_sont_critiques():
    profile = profile_for(TitleKind.DEMANDE_PRIX)
    assert "objet" in profile.critical_fields
    assert "autorite_emettrice" in profile.critical_fields


def test_la_nature_de_l_annonce_promeut_des_champs():
    # `date_limite_depot` n'est pas `required` dans le YAML, mais une demande
    # de prix sans date limite est inutilisable.
    assert profile_for(TitleKind.DEMANDE_PRIX).priority("date_limite_depot") is Priority.CRITICAL
    assert profile_for(TitleKind.OBJET).priority("date_limite_depot") is Priority.OPTIONAL


def test_une_promotion_ne_retrograde_jamais_un_champ_requis():
    # `montant_previsionnel_global` est rétrogradé pour les AMI, mais un champ
    # `required` du YAML doit rester critique en toute circonstance.
    profile = profile_for(TitleKind.MANIFESTATION_INTERET)
    assert profile.priority("objet") is Priority.CRITICAL
    assert profile.priority("montant_previsionnel_global") is Priority.OPTIONAL


def test_les_resultats_utilisent_le_schema_resultats():
    profile = profile_for(TitleKind.RESULTATS)
    assert profile.section == "RESULTATS"
    assert "attributaire_provisoire" in profile.critical_fields
    assert "date_limite_depot" not in profile.priorities


def test_une_nature_inconnue_retombe_sur_le_profil_avis():
    # Mieux vaut contrôler une annonce mal classée que de la laisser sans contrôle.
    profile = profile_for(TitleKind.UNKNOWN)
    assert profile.section == "AVIS" and profile.priorities


def test_le_poids_total_reflete_les_priorites():
    profile = profile_for(TitleKind.DEMANDE_PRIX)
    assert profile.total_weight == sum(int(p) for p in profile.priorities.values())
    assert profile.total_weight > len(profile.priorities)
