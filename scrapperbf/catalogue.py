"""Catalogue d'annonces : la chaîne complète, d'un ou plusieurs PDF au dashboard.

Le besoin que ce module sert est le besoin d'origine du projet : un bulletin
fait soixante pages et plusieurs dizaines d'annonces, dont deux ou trois
concernent un lecteur donné. Les trouver à l'œil prend une heure. Le catalogue
rassemble donc, pour chaque annonce et sans intervention humaine :

* son en-tête lu dans la maquette (émetteur, catégorie, objet, pages) ;
* son classement (secteur, région, portée, type d'émetteur) ;
* ses valeurs clés extraites par motifs, chacune avec sa justification ;
* sa note de fiabilité et ses alertes.

Le tout est sérialisable, donc utilisable aussi bien par le dashboard HTML que
par une base de données ou un export tableur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from .core.notices import Notice, default_backend, extract_notices
from .core.priority import profile_for
from .core.titles import TitleKind, analyze_title
from .extract.cascade import CascadeResult, essential_coverage, run_cascade
from .qa.alerts import Alert, sort_alerts, summarize
from .qa.integrity import check_integrity, notices_per_page
from .qa.judge import judge

# Correspondance entre la catégorie imprimée dans la bande rouge et la nature
# d'annonce du référentiel de champs. C'est la nature qui décide des champs
# attendus, donc cette correspondance pilote tout le contrôle qualité.
CATEGORIE_VERS_NATURE: dict[str, TitleKind] = {
    "Travaux": TitleKind.APPEL_OFFRES,
    "Fournitures et Services courants": TitleKind.DEMANDE_PRIX,
    "Prestations intellectuelles": TitleKind.MANIFESTATION_INTERET,
}


@dataclass
class CatalogueEntry:
    """Une annonce, vue par le catalogue."""

    notice: Notice
    kind: TitleKind
    cascade: CascadeResult | None = None
    reliability: float | None = None
    coverage: float = 0.0
    alerts: list[Alert] = field(default_factory=list)

    @property
    def record(self) -> dict:
        return self.cascade.record if self.cascade else {}

    @property
    def reliability_percent(self) -> int | None:
        return round(self.reliability * 100) if self.reliability is not None else None

    def to_dict(self) -> dict:
        base = self.notice.to_dict()
        base.update({
            "nature": self.kind.value,
            "coverage": round(self.coverage, 3),
            "coverage_percent": round(self.coverage * 100),
            "reliability": round(self.reliability, 3) if self.reliability is not None else None,
            "reliability_percent": self.reliability_percent,
            "record": self.record,
            "provenance": (
                {n: e.to_dict() for n, e in self.cascade.fields.items()} if self.cascade else {}
            ),
            "alerts": [a.to_dict() for a in sort_alerts(self.alerts)],
            "needs_admin": bool(self.cascade and self.cascade.needs_admin),
            "body": self.notice.body,
        })
        return base


@dataclass
class Catalogue:
    """Ensemble d'annonces issues d'un ou plusieurs bulletins."""

    entries: list[CatalogueEntry] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    generated_at: str = ""

    @property
    def stats(self) -> dict:
        def compte(cle: Callable[[CatalogueEntry], object]) -> dict:
            out: dict[str, int] = {}
            for e in self.entries:
                valeur = cle(e)
                nom = str(valeur) if valeur not in (None, "") else "—"
                out[nom] = out.get(nom, 0) + 1
            return dict(sorted(out.items(), key=lambda kv: -kv[1]))

        fiabilites = [e.reliability for e in self.entries if e.reliability is not None]
        alertes = sort_alerts([a for e in self.entries for a in e.alerts])
        return {
            "annonces": len(self.entries),
            "documents": self.documents,
            "secteurs": compte(lambda e: e.notice.classification.sector.label
                               if e.notice.classification else None),
            "regions": compte(lambda e: e.notice.classification.region
                              if e.notice.classification else None),
            "categories": compte(lambda e: e.notice.categorie_canonique),
            "portees": compte(lambda e: e.notice.portee),
            "emetteurs": compte(lambda e: e.notice.classification.emitter_type.label
                                if e.notice.classification else None),
            "fiabilite_moyenne": round(sum(fiabilites) / len(fiabilites), 3) if fiabilites else None,
            "a_relire": sum(1 for e in self.entries if e.cascade and e.cascade.needs_admin),
            "alertes": summarize(alertes),
        }

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "documents": self.documents,
            "stats": self.stats,
            "annonces": [e.to_dict() for e in self.entries],
        }

    def filter(
        self,
        *,
        secteur: str | None = None,
        region: str | None = None,
        categorie: str | None = None,
        portee: str | None = None,
        fiabilite_min: float | None = None,
        recherche: str | None = None,
    ) -> list[CatalogueEntry]:
        """Filtre le catalogue, les critères se combinant par ET.

        Le même filtrage est offert côté Python et côté dashboard : ce qu'un
        lecteur voit à l'écran, un traitement automatique peut le refaire.
        """
        from .core.text import normalize

        besoin = normalize(recherche) if recherche else None
        retenues = []
        for e in self.entries:
            c = e.notice.classification
            if secteur and not (c and (c.sector.value == secteur or secteur in c.secondary_sectors)):
                continue
            if region and not (c and c.region == region):
                continue
            if categorie and e.notice.categorie_canonique != categorie:
                continue
            if portee and e.notice.portee != portee:
                continue
            if fiabilite_min is not None and (e.reliability or 0.0) < fiabilite_min:
                continue
            if besoin:
                foin = normalize(
                    " ".join([e.notice.objet, e.notice.emetteur, e.notice.body[:4000]])
                )
                if besoin not in foin:
                    continue
            retenues.append(e)
        return retenues


def build_catalogue(
    paths: list[str],
    *,
    backend=None,
    bulletin_dates: dict[str, date] | None = None,
    ask_llm: Callable[[str, list[str]], dict] | None = None,
    with_judge: bool = True,
) -> Catalogue:
    """Construit le catalogue d'un ou plusieurs PDF.

    Sans ``ask_llm``, aucun appel d'API n'est fait : le catalogue repose alors
    sur la maquette et les motifs, ce qui suffit à trier et à chercher. Le
    modèle n'est utile que pour compléter les champs que les motifs manquent.
    """
    from datetime import datetime, timezone

    backend = backend or default_backend()
    if backend is None:
        raise RuntimeError(
            "PyMuPDF requis pour lire la maquette des PDF : pip install pymupdf"
        )

    catalogue = Catalogue(generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    bulletin_dates = bulletin_dates or {}

    for path in paths:
        annonces = extract_notices(path, backend)
        if not annonces:
            # Un supplément « (bis) » ne contient que des résultats : aucune
            # annonce n'y est attendue, et le dire vaut mieux que de se taire.
            catalogue.documents.append(f"{path.rsplit('/', 1)[-1]} (aucune annonce)")
            continue
        catalogue.documents.append(annonces[0].document)
        par_page = notices_per_page([a.page_debut for a in annonces])

        for annonce in annonces:
            kind = _nature(annonce)
            profile = profile_for(kind)
            integrite = check_integrity(
                annonce.id, annonce.pages, annonce.body,
                notices_per_page=par_page,
                ends_with_signature=_semble_complet(annonce.body),
            )

            parution = bulletin_dates.get(annonce.document)
            rate = None
            if with_judge:
                def rate(record: dict, texte: str, _p=profile, _pa=parution, _id=annonce.id) -> float:
                    return judge(_id, record, _p, source=texte, bulletin_date=_pa).score

            cascade = run_cascade(
                annonce.id, annonce.body, profile,
                header=annonce.header, integrity=integrite,
                ask_llm=ask_llm, rate=rate,
            )
            couverture, _ = essential_coverage(cascade.record, profile)
            catalogue.entries.append(CatalogueEntry(
                notice=annonce, kind=kind, cascade=cascade,
                reliability=cascade.reliability, coverage=couverture,
                alerts=cascade.alerts,
            ))
    return catalogue


def _nature(annonce: Notice) -> TitleKind:
    """Nature de l'annonce : son intitulé d'abord, sa catégorie à défaut.

    L'intitulé du corps (« Avis à manifestation d'intérêt ») est plus précis que
    la catégorie de la bande rouge, qui ne dit que le domaine du marché.
    """
    for ligne in annonce.body_lines[:6]:
        analyse = analyze_title(ligne)
        if analyse.starts_notice and analyse.kind is not TitleKind.OBJET:
            return analyse.kind
    return CATEGORIE_VERS_NATURE.get(annonce.categorie_canonique or "", TitleKind.DEMANDE_PRIX)


def _semble_complet(body: str) -> bool:
    """L'annonce porte-t-elle une formule de clôture ?"""
    import re

    from .qa.multipage import SIGNATURE_RE

    from .core.text import normalize

    queue = normalize(body[-700:])
    return bool(SIGNATURE_RE.search(queue)) or bool(re.search(r"president|directeur|maire", queue))
