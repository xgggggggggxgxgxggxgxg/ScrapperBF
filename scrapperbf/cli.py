"""Ligne de commande de la plateforme de contrôle qualité.

    python -m scrapperbf audit quotidien_4466.md --numero N4466 --dashboard qa.html
    python -m scrapperbf ocr bulletin.pdf --out quotidien_4466.md
    python -m scrapperbf titles quotidien_4466.md
    python -m scrapperbf feedback import revue-N4466.json
    python -m scrapperbf feedback stats

L'audit fonctionne **sans appel LLM** : sur la seule structure du document, il
dit déjà si le PDF est sain (pages dupliquées, annonces tronquées, titres non
classés). C'est volontaire : écarter un bulletin défectueux avant l'extraction
évite d'en payer les jetons.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .core.pages import parse_file
from .core.priority import priority_table, profile_for
from .core.segment import segment
from .core.titles import analyze_title
from .dashboard.build import write_dashboard
from .feedback.store import FeedbackStore
from .qa.report import audit_document


def _load_extractions(path: str | Path, notices: list) -> dict[str, dict]:
    """Charge les extractions de la couche 3 et les apparie aux annonces.

    Deux formats acceptés :

    * ``{"AVIS#1": {...}}`` — déjà indexé par identifiant d'annonce ;
    * ``{"AVIS": [{...}, ...]}`` — la sortie historique de ``pipeline.py``,
      appariée dans l'ordre des annonces de la section. L'appariement positionnel
      est fragile, donc tout écart de cardinalité est signalé plutôt que masqué.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data and all(isinstance(v, dict) for v in data.values()) and any("#" in k for k in data):
        return data

    mapped: dict[str, dict] = {}
    for section in ("AVIS", "RESULTATS"):
        records = data.get(section) or []
        section_notices = [n for n in notices if n.section == section]
        if len(records) != len(section_notices):
            print(
                f"[attention] {len(records)} extraction(s) {section} pour "
                f"{len(section_notices)} annonce(s) segmentée(s) : appariement positionnel "
                "approximatif, les écarts apparaîtront comme extractions manquantes.",
                file=sys.stderr,
            )
        for notice, record in zip(section_notices, records):
            mapped[notice.id] = record
    return mapped


def cmd_audit(args: argparse.Namespace) -> int:
    doc = parse_file(args.markdown)
    notices = segment(doc)
    extractions = _load_extractions(args.extractions, notices) if args.extractions else None
    store = FeedbackStore(args.feedback_db) if args.use_feedback else None

    report = audit_document(
        doc,
        document_name=args.numero or Path(args.markdown).stem,
        extractions=extractions,
        store=store,
    )
    print(report.to_text(max_alerts=args.max_alerts))

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nRapport JSON  -> {args.json}")
    if args.dashboard:
        path = write_dashboard(report, args.dashboard)
        print(f"Dashboard     -> {path}")

    # Code de sortie exploitable en CI / cron : non nul si le bulletin demande
    # une intervention humaine.
    critical = report.stats["alertes"]["critique"]
    if args.fail_on_critical and critical:
        return 2
    return 0


def cmd_ocr(args: argparse.Namespace) -> int:
    from .ocr.light_ocr import extract_pdf

    report = extract_pdf(args.pdf, lang=args.lang, dpi=args.dpi, force_ocr=args.force_ocr)
    summary = report.to_dict()
    print(
        f"{summary['page_count']} pages | confiance moyenne {summary['mean_confidence']:.0%} "
        f"| OCR appliqué sur {len(summary['ocr_pages'])} page(s) {summary['ocr_pages'] or ''}"
    )
    if summary["weak_pages"]:
        print(f"Pages de qualité douteuse (à contrôler) : {summary['weak_pages']}")
    if args.out:
        Path(args.out).write_text(report.to_markdown(), encoding="utf-8")
        print(f"Markdown -> {args.out}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Rapport OCR -> {args.json}")
    return 0


def cmd_titles(args: argparse.Namespace) -> int:
    """Inspecte le tri par titre — l'outil de diagnostic du classement."""
    doc = parse_file(args.markdown)
    print(f"{len(doc.pages)} pages\n")
    for page in doc.pages:
        for block in page.blocks:
            if block.type != "title":
                continue
            a = analyze_title(block.text)
            print(f"p.{page.page_number:>4} {a.kind.value:22} {a.confidence:>5.2f}  {block.text[:70]}")
            if args.verbose and a.evidence:
                print(f"{'':12}  ↳ {'; '.join(a.evidence)}")
    if args.profiles:
        print("\n--- Champs attendus par nature d'annonce ---")
        print(priority_table())
    return 0


def cmd_bands(args: argparse.Namespace) -> int:
    """Index des annonces lu dans la maquette (bandes colorées du PDF)."""
    from .core.layout import default_backend, describe, extract_bands, group_notices

    backend = default_backend()
    if backend is None:
        print("PyMuPDF requis pour lire la maquette : pip install pymupdf", file=sys.stderr)
        return 1

    bands = extract_bands(args.pdf, backend)
    notices = group_notices(bands)
    if args.json:
        Path(args.json).write_text(
            json.dumps({
                "pdf": args.pdf,
                "bandes": [b.to_dict() for b in bands],
                "annonces": [n.to_dict() for n in notices],
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"Index JSON -> {args.json}")
    print(describe(notices))
    if not notices:
        # Un supplément « (bis) » ne contient que des résultats : aucune bande
        # d'annonce n'y est donc attendue.
        print("\nAucune annonce : document de résultats seuls, ou maquette différente.")
    return 0


def cmd_feedback(args: argparse.Namespace) -> int:
    store = FeedbackStore(args.feedback_db)
    if args.action == "import":
        payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
        counts = store.import_reviews(payload, reviewer=args.reviewer)
        print(f"Importé : {counts['reviews']} revue(s), {counts['corrections']} correction(s), "
              f"{counts['alertes']} jugement(s) d'alerte.")
    elif args.action == "stats":
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    elif args.action == "prompt":
        supplement = store.prompt_supplement(args.kind)
        print(supplement or "Aucun retour humain exploitable pour l'instant.")
    return 0


def cmd_fields(args: argparse.Namespace) -> int:
    from .core.titles import TitleKind

    kind = TitleKind(args.kind)
    profile = profile_for(kind)
    print(f"{kind.value} → section {profile.section}")
    for name, priority in sorted(profile.priorities.items(), key=lambda kv: (-int(kv[1]), kv[0])):
        print(f"  {priority.label:>9}  {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrapperbf",
        description="Contrôle qualité des annonces de marchés publics (Quotidien DGCMEF)",
    )
    parser.add_argument("--feedback-db", default=None, help="Chemin de la base de retours humains")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("audit", help="Auditer un bulletin Markdown")
    p.add_argument("markdown")
    p.add_argument("--numero", help="Identifiant du bulletin (ex: N4466)")
    p.add_argument("--extractions", help="JSON des extractions de la couche 3")
    p.add_argument("--json", help="Écrire le rapport JSON complet")
    p.add_argument("--dashboard", help="Écrire le dashboard HTML de vérification")
    p.add_argument("--max-alerts", type=int, default=40)
    p.add_argument("--use-feedback", action="store_true",
                   help="Calmer les alertes jugées faux positifs par les opérateurs")
    p.add_argument("--fail-on-critical", action="store_true",
                   help="Code de sortie 2 si une alerte critique est présente")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("ocr", help="Extraire le texte d'un PDF (OCR léger, page par page)")
    p.add_argument("pdf")
    p.add_argument("--out", help="Markdown de sortie")
    p.add_argument("--json", help="Rapport OCR JSON")
    p.add_argument("--lang", default="fra")
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--force-ocr", action="store_true", help="Ignorer la couche de texte intégrée")
    p.set_defaults(func=cmd_ocr)

    p = sub.add_parser("titles", help="Inspecter la classification des titres")
    p.add_argument("markdown")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--profiles", action="store_true", help="Afficher les champs attendus par nature")
    p.set_defaults(func=cmd_titles)

    p = sub.add_parser("fields", help="Champs attendus pour une nature d'annonce")
    p.add_argument("kind", help="ex: DEMANDE_PRIX, APPEL_OFFRES, RESULTATS")
    p.set_defaults(func=cmd_fields)

    p = sub.add_parser("bands", help="Index des annonces lu dans la maquette du PDF")
    p.add_argument("pdf")
    p.add_argument("--json", help="Écrire l'index en JSON")
    p.set_defaults(func=cmd_bands)

    p = sub.add_parser("feedback", help="Gérer les retours humains")
    p.add_argument("action", choices=["import", "stats", "prompt"])
    p.add_argument("file", nargs="?", help="Fichier JSON exporté par le dashboard (action import)")
    p.add_argument("--reviewer", default="anonyme")
    p.add_argument("--kind", help="Nature d'annonce (action prompt)")
    p.set_defaults(func=cmd_feedback)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.feedback_db is None:
        from .feedback.store import DEFAULT_DB

        args.feedback_db = str(DEFAULT_DB)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
