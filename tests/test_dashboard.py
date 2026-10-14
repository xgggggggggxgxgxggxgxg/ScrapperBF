import json
import re

from scrapperbf.dashboard.build import render_dashboard, write_dashboard
from scrapperbf.qa.report import audit_text


def rapport(bulletin, extraction):
    return audit_text(bulletin, "N4466", extractions={"AVIS#1": extraction})


def test_le_dashboard_embarque_le_rapport(bulletin, extraction_demande_prix):
    html = render_dashboard(rapport(bulletin, extraction_demande_prix))
    payload = re.search(
        r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S
    ).group(1)
    data = json.loads(payload.replace("<\\/", "</"))
    assert data["document"] == "N4466" and data["notices"]


def test_le_json_embarque_ne_peut_pas_fermer_la_balise(bulletin, extraction_demande_prix):
    report = rapport(bulletin, extraction_demande_prix)
    report.notices[0].notice.title = 'Piege </script><img src=x onerror=alert(1)>'
    html = render_dashboard(report)
    bloc = html.split('<script id="report-data" type="application/json">')[1].split("</script>")[0]
    assert "</script>" not in bloc and "<\\/script>" in bloc


def test_le_dashboard_est_autonome(bulletin, extraction_demande_prix):
    html = render_dashboard(rapport(bulletin, extraction_demande_prix))
    # Aucune ressource réseau : utilisable hors ligne, à côté du PDF.
    assert "http://" not in html and "https://" not in html
    assert "<script src=" not in html and "<link" not in html


def test_les_colonnes_attendues_sont_presentes(bulletin, extraction_demande_prix):
    html = render_dashboard(rapport(bulletin, extraction_demande_prix))
    for colonne in ("Annonce", "Pages", "Nature", "Confiance", "Alertes", "Verdict"):
        assert f">{colonne}<" in html


def test_l_export_des_corrections_est_cable(bulletin, extraction_demande_prix):
    html = render_dashboard(rapport(bulletin, extraction_demande_prix))
    assert "Exporter les corrections" in html
    assert "revue-" in html and "faux_positif" in html


def test_ecriture_sur_disque(tmp_path, bulletin, extraction_demande_prix):
    path = write_dashboard(rapport(bulletin, extraction_demande_prix), tmp_path / "sub" / "qa.html")
    assert path.exists() and path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
