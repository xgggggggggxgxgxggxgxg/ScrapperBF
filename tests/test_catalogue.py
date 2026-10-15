"""Catalogue et dashboard de recherche."""

import json
import re

import pytest

from scrapperbf.catalogue import build_catalogue
from scrapperbf.dashboard.catalogue_view import render_catalogue
from tests.test_notices import FauxPdf, PAGE_CONTINUATION, PAGE_OUVERTURE, fill, mot

CYAN, JAUNE, ROUGE = (0.0, 1.0, 1.0), (1.0, 1.0, 0.0), (1.0, 0.0, 0.0)

PAGE_SANTE = (
    [fill(ROUGE, 57, 248, 17, 180), fill(JAUNE, 83), fill(CYAN, 102, 520, 14)],
    [mot("Fournitures", 190, 59), mot("et", 265, 59), mot("Services", 285, 59),
     mot("courants", 350, 59),
     mot("MINISTERE", 60, 85), mot("DE", 145, 85), mot("LA", 170, 85), mot("SANTE", 195, 85),
     mot("Acquisition", 60, 104), mot("de", 140, 104), mot("médicaments", 165, 104)],
    [(59, "Fournitures et Services courants"), (85, "MINISTERE DE LA SANTE"),
     (104, "Acquisition de médicaments"),
     (160, "Avis de demande de prix"),
     (170, "n° : 2026-45/MS/SG/DMP"),
     (180, "Financement : Budget de l'Etat, exercice 2026"),
     (190, "Les offres devront parvenir au plus tard le 20/10/2026 à 09 heures 00."),
     (200, "L'ouverture des plis sera faite immédiatement."),
     (210, "Le délai d'exécution est de trente (30) jours."),
     (220, "Le Directeur Général des marchés publics.")],
)


@pytest.fixture
def catalogue(monkeypatch):
    backend = FauxPdf([PAGE_OUVERTURE, PAGE_CONTINUATION, PAGE_SANTE])
    return build_catalogue(["N4503.pdf"], backend=backend)


def test_le_catalogue_rassemble_les_annonces(catalogue):
    assert len(catalogue.entries) == 2
    assert catalogue.documents == ["N4503"]


def test_chaque_annonce_est_classee_et_notee(catalogue):
    sante = next(e for e in catalogue.entries if "médicaments" in e.notice.objet)
    assert sante.notice.classification.sector.value == "SANTE"
    assert sante.reliability is not None and 0 <= sante.reliability <= 1
    assert sante.coverage > 0


def test_les_valeurs_cles_sont_extraites_sans_appel_d_api(catalogue):
    sante = next(e for e in catalogue.entries if "médicaments" in e.notice.objet)
    record = sante.record
    assert record["financement"].startswith("Budget de l'Etat")
    assert "20/10/2026" in str(record["date_limite_depot"])
    # Aucun fournisseur n'a été interrogé.
    assert not sante.cascade.llm_called


def test_la_provenance_de_chaque_valeur_est_conservee(catalogue):
    sante = next(e for e in catalogue.entries if "médicaments" in e.notice.objet)
    provenance = sante.to_dict()["provenance"]
    assert provenance["autorite_emettrice"]["source"] == "maquette"
    assert provenance["financement"]["source"] == "etiquette"
    assert provenance["financement"]["evidence"]


def test_les_statistiques_comptent_par_axe(catalogue):
    stats = catalogue.stats
    assert stats["annonces"] == 2
    assert "Santé" in stats["secteurs"]
    assert "Guiriko" in stats["regions"]
    assert set(stats["portees"]) <= {"NATIONAL", "REGIONAL", "INDETERMINE"}


# --- filtrage ---------------------------------------------------------------

def test_le_filtre_par_secteur(catalogue):
    assert len(catalogue.filter(secteur="SANTE")) == 1


def test_le_filtre_retient_aussi_les_secteurs_secondaires(catalogue):
    sante = next(e for e in catalogue.entries if "médicaments" in e.notice.objet)
    sante.notice.classification.secondary_sectors = ["INFORMATIQUE"]
    assert len(catalogue.filter(secteur="INFORMATIQUE")) == 1


def test_le_filtre_par_region(catalogue):
    assert len(catalogue.filter(region="Guiriko")) == 1
    assert catalogue.filter(region="Sahel") == []


def test_les_filtres_se_combinent_par_et(catalogue):
    assert catalogue.filter(secteur="SANTE", region="Guiriko") == []
    assert len(catalogue.filter(secteur="SANTE", portee="NATIONAL")) == 1


def test_la_recherche_plein_texte(catalogue):
    assert len(catalogue.filter(recherche="médicaments")) == 1
    # Insensible à la casse et aux accents.
    assert len(catalogue.filter(recherche="MEDICAMENTS")) == 1
    assert catalogue.filter(recherche="tracteurs") == []


def test_le_filtre_par_fiabilite(catalogue):
    assert catalogue.filter(fiabilite_min=1.01) == []
    assert len(catalogue.filter(fiabilite_min=0.0)) == 2


# --- dashboard --------------------------------------------------------------

def test_le_dashboard_embarque_le_catalogue(catalogue):
    html = render_catalogue(catalogue)
    bloc = re.search(r'<script id="catalogue-data" type="application/json">(.*?)</script>',
                     html, re.S).group(1)
    data = json.loads(bloc.replace("<\\/", "</"))
    assert len(data["annonces"]) == 2


def test_le_dashboard_est_autonome(catalogue):
    html = render_catalogue(catalogue)
    # Aucune ressource réseau : utilisable hors ligne, à côté du PDF.
    assert "<script src=" not in html and "<link" not in html


def test_le_json_embarque_ne_peut_pas_fermer_la_balise(catalogue):
    catalogue.entries[0].notice.objet = 'Piège </script><img src=x onerror=alert(1)>'
    html = render_catalogue(catalogue)
    bloc = html.split('<script id="catalogue-data" type="application/json">')[1].split("</script>")[0]
    assert "</script>" not in bloc


def test_le_dashboard_expose_les_filtres_demandes(catalogue):
    html = render_catalogue(catalogue)
    for id_ in ("f-secteur", "f-region", "f-categorie", "f-portee", "f-emetteur",
                "f-document", "f-fiab", "f-relire"):
        assert f'id="{id_}"' in html
    assert "Exporter la sélection (CSV)" in html


def test_le_dashboard_montre_le_pourcentage_et_pas_la_couleur_seule(catalogue):
    html = render_catalogue(catalogue)
    # Une couleur de statut ne doit jamais porter l'information à elle seule.
    assert "class=\"pct\"" in html and "Math.round(v * 100)" in html
