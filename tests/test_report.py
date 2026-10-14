import json

from scrapperbf.core.titles import TitleKind
from scrapperbf.qa.alerts import Code, Severity
from scrapperbf.qa.report import audit_text
from scrapperbf.qa.scoring import Verdict


def audit_du_bulletin(bulletin, extractions=None, store=None):
    return audit_text(bulletin, "N4466", extractions=extractions, store=store)


def test_audit_structurel_sans_appel_llm(bulletin):
    # Aucune extraction fournie : le rapport contrôle la structure seule, ce
    # qui permet d'écarter un PDF défectueux avant de dépenser des jetons.
    report = audit_du_bulletin(bulletin)
    assert report.stats["annonces"] >= 4
    assert all(a.score.verdict is Verdict.A_VERIFIER for a in report.notices)
    assert Code.EXTRACTION_ECHOUEE not in [a.code for a in report.all_alerts]


def test_extraction_manquante_est_signalee(bulletin, extraction_demande_prix):
    report = audit_du_bulletin(bulletin, extractions={"AVIS#1": extraction_demande_prix})
    codes = [a.code for a in report.all_alerts]
    assert Code.EXTRACTION_ECHOUEE in codes


def test_une_bonne_extraction_est_validee(bulletin, extraction_demande_prix):
    report = audit_du_bulletin(bulletin, extractions={"AVIS#1": extraction_demande_prix})
    avis = next(a for a in report.notices if a.notice.id == "AVIS#1")
    assert avis.score.verdict is Verdict.VALIDE


def test_le_titre_non_classe_est_signale_avec_sa_page(bulletin):
    report = audit_du_bulletin(bulletin)
    alerte = next(a for a in report.all_alerts if a.code == Code.TITRE_NON_CLASSE)
    assert alerte.page_refs == [17] and "Communique" in alerte.message


def test_les_problemes_de_structure_remontent_au_niveau_document(bulletin_doublon):
    report = audit_text(bulletin_doublon, "N4466")
    codes = [a.code for a in report.document_alerts]
    assert Code.PAGE_DUPLIQUEE in codes
    assert Code.NUMEROTATION_PAGE_ANORMALE in codes


def test_les_alertes_sont_triees_par_gravite(bulletin_doublon):
    alerts = audit_text(bulletin_doublon, "N4466").all_alerts
    severites = [int(a.severity) for a in alerts]
    assert severites == sorted(severites, reverse=True)


def test_les_champs_prioritaires_dependent_de_la_nature(bulletin, extraction_demande_prix):
    report = audit_du_bulletin(bulletin, extractions={"AVIS#1": extraction_demande_prix})
    avis = next(a for a in report.notices if a.notice.kind is TitleKind.DEMANDE_PRIX)
    assert "date_limite_depot" in avis.priority_fields["critique"]


def test_un_code_calme_par_les_relectures_voit_sa_gravite_baisser(bulletin, store):
    for i in range(5):
        store.record_alert_feedback("N4466", Code.TITRE_NON_CLASSE, "faux_positif", f"AVIS#{i}")
    report = audit_du_bulletin(bulletin, store=store)
    alerte = next(a for a in report.all_alerts if a.code == Code.TITRE_NON_CLASSE)
    # Calmée, pas supprimée : un faux positif fréquent reste parfois vrai.
    assert alerte.severity is Severity.INFO
    assert "faux positifs" in alerte.hint
    assert Code.TITRE_NON_CLASSE in report.muted_codes


def test_les_statistiques_resument_le_bulletin(bulletin, extraction_demande_prix):
    stats = audit_du_bulletin(bulletin, extractions={"AVIS#1": extraction_demande_prix}).stats
    assert stats["pages"] == 6
    assert stats["a_relire"] >= 1
    assert sum(stats["verdicts"].values()) == stats["annonces"]
    assert stats["alertes"]["total"] == sum(stats["codes_alertes"].values())


def test_le_rapport_est_serialisable(bulletin, extraction_demande_prix):
    report = audit_du_bulletin(bulletin, extractions={"AVIS#1": extraction_demande_prix})
    payload = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    assert payload["document"] == "N4466"
    assert payload["notices"][0]["id"].startswith(("AVIS#", "RESULTATS#"))
    alertes = [a for n in payload["notices"] for a in n["alerts"]]
    assert alertes and all("human" in a and "pages_label" in a for a in alertes)


def test_le_resume_texte_cite_les_pages(bulletin):
    texte = audit_du_bulletin(bulletin).to_text()
    assert "Rapport QA — N4466" in texte and "p. " in texte


def test_chaque_annonce_connait_son_groupe_de_pages(bulletin):
    report = audit_du_bulletin(bulletin)
    travaux = next(a for a in report.notices if a.notice.marche_type == "Travaux")
    assert travaux.span_pages == [13, 14]
