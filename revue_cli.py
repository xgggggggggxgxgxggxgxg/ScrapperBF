"""
CLI de REVUE HUMAINE — lister les dossiers en attente et saisir les corrections.

C'est l'interface humaine de la file produite par le juge (🟡 jaune / 🔴 rouge).
Chaque correction se fait en DEUX champs (quoi_faire + valeur_correcte) et crée
automatiquement une règle réutilisable (regles.py), qui sera réinjectée dans le
prompt des extracteurs lors des traitements suivants.

Exemples :
    python revue_cli.py                      # liste les dossiers en attente
    python revue_cli.py --tous               # liste tous les dossiers
    python revue_cli.py show N4503-AVIS-2    # détail d'un dossier
    python revue_cli.py corriger N4503-AVIS-2 montant_previsionnel_global \
        --valeur '{"ttc": "80 000 000"}' \
        --quoi "recopier le montant TTC écrit en toutes lettres" \
        --declencheurs htva,ttc
    python revue_cli.py regles               # liste les règles apprises

Aucune dépendance réseau. Lecture/écriture dans output/revue_humaine/ et regles.json.
"""

import argparse
import json
import sys

import revue_humaine
import regles

# Petites couleurs ANSI (désactivées si la sortie n'est pas un terminal).
_TTY = sys.stdout.isatty()
_C = {"rouge": "\033[31m", "jaune": "\033[33m", "vert": "\033[32m",
      "gras": "\033[1m", "gris": "\033[90m", "reset": "\033[0m"}


def _c(txt, couleur):
    if not _TTY:
        return str(txt)
    return f"{_C.get(couleur, '')}{txt}{_C['reset']}"


def _valeur(brut: str):
    """Interprète --valeur : JSON si possible (objet/liste/nombre), sinon chaîne."""
    try:
        return json.loads(brut)
    except (ValueError, TypeError):
        return brut


def cmd_list(args):
    statut = None if args.tous else "en_attente"
    dossiers = revue_humaine.lister(statut)
    if not dossiers:
        print("Aucun dossier" + ("" if args.tous else " en attente") + ".")
        return
    # Rouge d'abord (priorité haute), puis jaune.
    ordre = {"rouge": 0, "jaune": 1, "vert": 2}
    dossiers.sort(key=lambda d: (ordre.get(d.get("couleur"), 3), d.get("id", "")))
    print(_c(f"{len(dossiers)} dossier(s) :", "gras"))
    for d in dossiers:
        pastille = {"rouge": "🔴", "jaune": "🟡", "vert": "🟢"}.get(d.get("couleur"), "⚪")
        champs = ", ".join(c.get("champ", "?") for c in d.get("champs_en_conflit", [])) or "—"
        statut_lbl = "" if d.get("statut") == "en_attente" else _c(f" [{d.get('statut')}]", "gris")
        print(f"  {pastille} {_c(d['id'], 'gras')}{statut_lbl}  score={d.get('score')}  "
              f"champs: {champs}")
        print(_c(f"     {d.get('raison', '')}", "gris"))


def cmd_show(args):
    dossiers = {d["id"]: d for d in revue_humaine.lister()}
    d = dossiers.get(args.id)
    if not d:
        print(f"Dossier introuvable : {args.id}", file=sys.stderr)
        sys.exit(1)
    pastille = {"rouge": "🔴", "jaune": "🟡", "vert": "🟢"}.get(d.get("couleur"), "⚪")
    print(_c(f"{pastille} {d['id']}  ({d.get('statut')})", "gras"))
    print(f"  raison          : {d.get('raison')}")
    if d.get("pourquoi_divergence_juges"):
        print(f"  désaccord juges : {_c(d['pourquoi_divergence_juges'], 'rouge')}")
    print(f"  score scoreur   : {d.get('score')}  "
          f"(scoreur={d.get('verdict_scoreur')}, juge-LLM={d.get('verdict_llm')})")
    if d.get("analyse_llm"):
        print(f"  analyse juge-LLM: {d.get('analyse_llm')}")
    print(_c("\n  Champs en conflit :", "gras"))
    for c in d.get("champs_en_conflit", []):
        crit = _c(" [CRITIQUE]", "rouge") if c.get("critique") else ""
        print(f"    - {c.get('champ')}{crit}  ({c.get('cause')})"
              f"  A={c.get('a')!r}  B={c.get('b')!r}")
    print(_c("\n  Extraction A :", "gras"))
    print("    " + json.dumps(d.get("extraction_a"), ensure_ascii=False))
    print(_c("  Extraction B :", "gras"))
    print("    " + json.dumps(d.get("extraction_b"), ensure_ascii=False))
    print(_c("\n  Champs proposés (valeurs retenues) :", "gras"))
    print("    " + json.dumps(d.get("champs_proposes"), ensure_ascii=False, indent=2).replace("\n", "\n    "))
    if d.get("corrections"):
        print(_c("\n  Corrections déjà appliquées :", "gras"))
        for corr in d["corrections"]:
            print(f"    - {corr['champ']} = {corr['valeur_correcte']!r}  ({corr['quoi_faire']})")
    print(_c("\n  Texte source :", "gras"))
    print(_c("    " + (d.get("texte", "") or "").replace("\n", "\n    "), "gris"))


def cmd_corriger(args):
    valeur = _valeur(args.valeur)
    declencheurs = [x.strip() for x in (args.declencheurs or "").split(",") if x.strip()]
    try:
        d = revue_humaine.appliquer_correction(
            args.id, args.champ, quoi_faire=args.quoi, valeur_correcte=valeur,
            declencheurs=declencheurs)
    except FileNotFoundError:
        print(f"Dossier introuvable : {args.id}", file=sys.stderr)
        sys.exit(1)
    print(_c(f"✓ {args.id} corrigé", "vert"))
    print(f"  {args.champ} = {valeur!r}")
    print(f"  statut -> {d.get('statut')}")
    if d.get("_regle_creee"):
        print(_c(f"  règle {d['_regle_creee']} créée (sera réinjectée dans les prochains runs)", "gras"))


def cmd_regles(args):
    rules = regles.load_rules()
    if not rules:
        print("Aucune règle apprise pour l'instant.")
        return
    print(_c(f"{len(rules)} règle(s) :", "gras"))
    for r in rules:
        decl = ", ".join(r.get("declencheurs") or []) or "(globale)"
        print(f"  {_c(r['id'], 'gras')} [{r.get('champ')}] déclencheurs: {decl}")
        print(_c(f"     {r.get('quoi_faire')}", "gris"))
        if r.get("exemple_valeur") is not None:
            print(_c(f"     ex. attendu : {json.dumps(r['exemple_valeur'], ensure_ascii=False)}", "gris"))


def build_parser():
    p = argparse.ArgumentParser(description="Revue humaine de la file du juge (ScrapperBF).")
    sub = p.add_subparsers(dest="cmd")

    pl = sub.add_parser("list", help="lister les dossiers (défaut)")
    pl.add_argument("--tous", action="store_true", help="inclure les dossiers déjà corrigés")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("show", help="détail d'un dossier")
    ps.add_argument("id")
    ps.set_defaults(func=cmd_show)

    pc = sub.add_parser("corriger", help="appliquer une correction (crée une règle)")
    pc.add_argument("id")
    pc.add_argument("champ")
    pc.add_argument("--valeur", required=True, help="valeur correcte (JSON ou texte brut)")
    pc.add_argument("--quoi", required=True, help="consigne : quoi faire dans cette situation")
    pc.add_argument("--declencheurs", default="", help="mots-clés déclencheurs, séparés par des virgules")
    pc.set_defaults(func=cmd_corriger)

    pr = sub.add_parser("regles", help="lister les règles apprises")
    pr.set_defaults(func=cmd_regles)

    # Défaut (aucune sous-commande) = list
    p.add_argument("--tous", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_list)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
