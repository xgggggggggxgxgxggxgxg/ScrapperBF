"""Construction du prompt et relevé des candidats."""

import pytest

from scrapperbf.extract.prompt import (
    build_prompt,
    build_schema,
    collect_candidates,
    known_fields,
    load_rules,
)

ANNONCE = """Avis de demande de prix
n° : 2026-23/MSECU/SG/ONASER/DMP
Financement : Budget ONASER, exercice 2026
Montant prévisionnel : vingt cinq millions (25 000 000) FCFA TTC.
Le délai d'exécution est de : vingt un (21) jours.
Téléphone : (00226) 25 37 44 78, E-mail : onaser.bf@gmail.com
Le dossier est retiré contre paiement de vingt cinq mille (25 000) FCFA.
Les offres devront parvenir avant le 15/10/2026 09h 00 mn GMT.
L'ouverture des plis sera faite immédiatement.
Les soumissionnaires resteront engagés pour soixante-dix (70) jours.
Président de la commission KABORE Etienne N°3827 – lundi 04 Mars 2024
N° 4503 – Mardi 06 octobre 2026"""


def test_le_referentiel_est_lisible():
    rules = load_rules()
    assert rules["version"] >= 1
    assert "date_limite_depot" in rules["champs"]
    # Chaque champ doit dire comment choisir : c'est la raison d'être du fichier.
    for name, spec in rules["champs"].items():
        assert spec.get("definition"), name
        assert spec.get("desambiguisation", {}).get("regle"), name


def test_les_candidats_sont_releves_par_nature():
    candidats = collect_candidates(ANNONCE)
    valeurs = {k: [c.value for c in v] for k, v in candidats.items()}
    assert "15/10/2026" in valeurs["dates"]
    # La date de pied de page d'un autre bulletin est aussi proposée : c'est au
    # modèle de l'écarter, et on veut qu'il la voie pour pouvoir la refuser.
    assert "04 Mars 2024" in valeurs["dates"]
    assert "(00226) 25 37 44 78" in valeurs["telephones"]
    assert any("25 000 000" in v for v in valeurs["montants"])
    assert any("70" in v for v in valeurs["durees"])


def test_un_numero_de_reference_n_est_pas_un_candidat_fantaisiste():
    # Accepter la lettre « o » comme signe degré faisait de « n|on
    # remboursable » une référence.
    candidats = collect_candidates("un montant non remboursable de 25 000 FCFA")
    assert not candidats.get("references")


def test_les_candidats_sont_dedoublonnes():
    texte = "avant le 15/10/2026 puis encore le 15/10/2026"
    assert len(collect_candidates(texte)["dates"]) == 1


def test_chaque_candidat_porte_son_contexte():
    candidat = collect_candidates(ANNONCE)["dates"][0]
    assert candidat.context and len(candidat.context) > 20
    assert candidat.to_line().startswith("  [dat")


def test_le_prompt_ne_porte_que_les_champs_demandes():
    p = build_prompt(ANNONCE, ["date_limite_depot"])
    assert p.fields == ["date_limite_depot"]
    assert "date_limite_depot" in p.user
    # Interroger sur douze champs quand un seul manque coûte douze fois plus.
    assert "garantie_soumission" not in p.user


def test_le_prompt_porte_les_regles_de_desambiguisation():
    p = build_prompt(ANNONCE, ["date_limite_depot", "date_ouverture_plis"])
    assert "Ne pas confondre avec « date_ouverture_plis »" in p.user
    assert "au plus tard le" in p.user


def test_le_prompt_liste_les_candidats_utiles_seulement():
    p = build_prompt(ANNONCE, ["date_limite_depot"])
    assert "DATES" in p.user
    # Aucun champ demandé ne porte sur les téléphones : ne pas les lister.
    assert "TELEPHONES" not in p.user


def test_les_acquis_de_la_maquette_sont_donnes_comme_tels():
    p = build_prompt(ANNONCE, ["objet"], header={"objet": "Régie médiatique du TOUR DU FASO"})
    assert "INFORMATIONS DÉJÀ ÉTABLIES" in p.user
    assert "Régie médiatique du TOUR DU FASO" in p.user


def test_la_date_du_bulletin_sert_de_borne():
    p = build_prompt(ANNONCE, ["date_limite_depot"], bulletin_date="mardi 06 octobre 2026")
    assert "postérieure" in p.user and "06 octobre 2026" in p.user


def test_un_champ_inconnu_est_signale_et_ignore():
    p = build_prompt(ANNONCE, ["date_limite_depot", "champ_imaginaire"])
    assert p.fields == ["date_limite_depot"]
    assert "champ_imaginaire" in p.user


def test_le_schema_impose_valeur_confiance_et_citation():
    schema = build_schema(["objet"])
    champ = schema["properties"]["objet"]
    # Sans ces trois éléments, aucune vérification automatique n'est possible.
    assert set(champ["required"]) == {"valeur", "confiance", "citation"}


def test_la_consigne_interdit_d_inventer():
    p = build_prompt(ANNONCE, ["objet"])
    assert "null" in p.system and "inventée" in p.system


def test_tous_les_champs_sont_demandables():
    p = build_prompt(ANNONCE, None)
    assert set(p.fields) == set(known_fields())


def test_les_candidats_peuvent_etre_desactives():
    p = build_prompt(ANNONCE, ["date_limite_depot"], with_candidates=False)
    assert "VALEURS CANDIDATES" not in p.user
