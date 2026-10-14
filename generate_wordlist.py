#!/usr/bin/env python3
"""
Génère une "wordlist" d'URLs candidates pour tous les PDF "Quotidien"
(dgcmef.gov.bf) depuis le 01/01/2022 jusqu'à aujourd'hui, en se basant sur :

  1. Les patterns de nommage de fichiers déjà identifiés (standard, bis,
     suffixes _0/_1, doubles numéros, variantes majuscule/minuscule, etc.)
  2. Une estimation du numéro d'édition attendu pour chaque mois, calculée
     par régression linéaire à partir des URLs connues (le numéro d'édition
     augmente d'environ ~21-22 par mois, de façon continue sur toute la
     période, indépendamment du dossier YYYY-MM).
  3. Les variantes de domaine observées (http/https, avec/sans www).

Le script ne "devine" pas au hasard : il calcule, pour chaque mois, une
fourchette plausible de numéros d'édition (centre estimé ± marge), puis
génère le produit cartésien {numéros} x {patterns de nom de fichier} x
{variantes de domaine} x {dossier YYYY-MM}.

Usage :
    # 1) Calibrer la régression à partir d'une liste d'URLs déjà connues
    #    (optionnel mais fortement recommandé pour affiner les fourchettes)
    python3 generate_wordlist.py --calibrate urls_quotidien.txt --out wordlist.txt

    # 2) Ou générer directement avec les valeurs de calibration par défaut
    #    (déjà calculées à partir du jeu de données fourni)
    python3 generate_wordlist.py --out wordlist.txt

Le fichier généré (wordlist.txt) peut ensuite être vérifié avec
check_urls.py (déjà fourni) pour ne garder que les URLs qui répondent 200 :

    python3 check_urls.py wordlist.txt
"""

import re
import sys
import random
import argparse
from datetime import datetime, date
from pathlib import Path
from calendar import monthrange


# ----------------------------------------------------------------------------
# 1. CONFIGURATION PAR DEFAUT (calibrée sur le jeu de données fourni)
# ----------------------------------------------------------------------------
START_DATE = date(2022, 1, 1)

# Point d'ancrage : au mois de départ (2022-01), le numéro d'édition est
# estimé à START_NUMBER. L'estimation pour les mois suivants est ensuite
# extrapolée avec la pente SLOPE (numéros/mois).
START_NUMBER = 3260
START_YM = (2022, 1)

# Régression linéaire : numero_estime = SLOPE * idx_mois + INTERCEPT
# idx_mois = annee*12 + mois  (valeurs calculées à partir de urls_quotidien.txt)
DEFAULT_SLOPE = 21.71
# L'intercept est calculé automatiquement à partir de l'ancrage START_NUMBER
# / START_YM ci-dessus (voir plus bas), pour que l'estimation à 2022-01
# corresponde exactement à START_NUMBER.
DEFAULT_INTERCEPT = START_NUMBER - DEFAULT_SLOPE * (START_YM[0] * 12 + START_YM[1])

# Marge (+/-) appliquée autour de l'estimation, aussi bien pour les mois
# connus que pour les mois sans donnée. Correspond à la marge demandée
# (par défaut 15 numéros de part et d'autre de l'estimation).
DEFAULT_MARGIN_UNKNOWN = 15
DEFAULT_MARGIN_KNOWN = 15

BASE_PATH = "/sites/default/files"

DOMAIN_VARIANTS = [
    "http://dgcmef.gov.bf",
    "http://www.dgcmef.gov.bf",
    "https://dgcmef.gov.bf",
]

# Patterns de nom de fichier à générer par numéro d'édition "n".
# Fonction lambda(n) -> nom de fichier. Classés par fréquence observée.
FILENAME_TEMPLATES = [
    lambda n: f"Quotidien N°{n}.pdf",
    lambda n: f"Quotidien N°{n}_0.pdf",
    lambda n: f"Quotidien N°{n}_1.pdf",
    lambda n: f"Quotidien n°{n}.pdf",
    lambda n: f"Quotidien N°{n} (bis).pdf",
    lambda n: f"Quotidien N°{n}(bis).pdf",
    lambda n: f"Quotidien N°{n} (bis)_C.pdf",
    lambda n: f"Quotidien N°{n}-{n+1}.pdf",
    lambda n: f"Quotidien n°{n}-{n+1}.pdf",
]


# ----------------------------------------------------------------------------
# 2. CALIBRATION (optionnelle) A PARTIR D'UN FICHIER D'URLS CONNUES
# ----------------------------------------------------------------------------
def month_index(year: int, month: int) -> int:
    return year * 12 + month


def calibrate_from_file(path: str):
    """
    Relit un fichier d'URLs connues pour :
      - recalculer la régression (slope, intercept)
      - construire un dict {(annee,mois): (min_num, max_num)} des numéros
        réellement observés par mois, pour resserrer la fourchette de ces
        mois-là.
    """
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    lines = [l.strip() for l in lines if l.strip()]

    date_re = re.compile(r'/sites/default/files/(\d{4})-(\d{2})/')
    num_re = re.compile(r'[Nn]°\s*(\d{3,4})')

    by_month = {}
    points = []
    for l in lines:
        dm = date_re.search(l)
        if not dm:
            continue
        y, mo = int(dm.group(1)), int(dm.group(2))
        fn = l.split("/")[-1]
        nm = num_re.search(fn)
        if not nm:
            continue
        num = int(nm.group(1))
        # on ignore les valeurs aberrantes trop faibles (mal extraites)
        if num < 2500:
            continue
        by_month.setdefault((y, mo), []).append(num)

    for (y, mo), nums in by_month.items():
        points.append((month_index(y, mo), sum(nums) / len(nums)))

    known_ranges = {
        ym: (min(nums), max(nums)) for ym, nums in by_month.items()
    }

    if len(points) >= 2:
        n = len(points)
        sx = sum(p[0] for p in points)
        sy = sum(p[1] for p in points)
        sxx = sum(p[0] ** 2 for p in points)
        sxy = sum(p[0] * p[1] for p in points)
        denom = (n * sxx - sx * sx)
        if denom != 0:
            slope = (n * sxy - sx * sy) / denom
            intercept = (sy - slope * sx) / n
            return slope, intercept, known_ranges

    return DEFAULT_SLOPE, DEFAULT_INTERCEPT, known_ranges


# ----------------------------------------------------------------------------
# 3. GENERATION DES MOIS ET DES FOURCHETTES DE NUMEROS
# ----------------------------------------------------------------------------
def iter_months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def estimate_range(y, m, slope, intercept, known_ranges,
                    margin_known=DEFAULT_MARGIN_KNOWN,
                    margin_unknown=DEFAULT_MARGIN_UNKNOWN):
    if (y, m) in known_ranges:
        lo, hi = known_ranges[(y, m)]
        return lo - margin_known, hi + margin_known
    center = slope * month_index(y, m) + intercept
    # La fourchette démarre PILE sur l'estimation (ex: 3260 pour 2022-01)
    # et s'étend seulement vers le haut, sur "margin_unknown" numéros.
    return int(round(center)), int(round(center + margin_unknown))


# ----------------------------------------------------------------------------
# 4. GENERATION DE LA WORDLIST
# ----------------------------------------------------------------------------
def generate_wordlist(start: date, end: date, slope, intercept, known_ranges,
                       margin_known=DEFAULT_MARGIN_KNOWN,
                       margin_unknown=DEFAULT_MARGIN_UNKNOWN,
                       templates=FILENAME_TEMPLATES,
                       domains=DOMAIN_VARIANTS):
    seen = set()
    total_months = 0
    for y, m in iter_months(start, end):
        total_months += 1
        lo, hi = estimate_range(y, m, slope, intercept, known_ranges,
                                 margin_known, margin_unknown)
        if lo < 1:
            lo = 1
        folder = f"{y:04d}-{m:02d}"
        for n in range(lo, hi + 1):
            for tmpl in templates:
                fname = tmpl(n)
                for domain in domains:
                    url = f"{domain}{BASE_PATH}/{folder}/{fname}"
                    if url not in seen:
                        seen.add(url)
                        yield url
    print(f"[info] {total_months} mois traités, {len(seen)} URLs candidates générées.",
          file=sys.stderr)


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--calibrate", help="Fichier d'URLs connues pour calibrer la régression et resserrer les mois connus", default=None)
    parser.add_argument("--out", default="wordlist.txt", help="Fichier de sortie (défaut: wordlist.txt)")
    parser.add_argument("--start", default="2022-01-01", help="Date de début (YYYY-MM-DD), défaut 2022-01-01")
    parser.add_argument("--end", default=None, help="Date de fin (YYYY-MM-DD), défaut = aujourd'hui")
    parser.add_argument("--margin-known", type=int, default=DEFAULT_MARGIN_KNOWN,
                         help="Marge (+/-) ajoutée autour du min/max connu pour un mois déjà documenté")
    parser.add_argument("--margin-unknown", type=int, default=DEFAULT_MARGIN_UNKNOWN,
                         help="Marge (+/-) autour de l'estimation pour un mois sans donnée connue")
    parser.add_argument("--shuffle", action="store_true",
                         help="Mélange aléatoirement l'ordre des URLs générées (par défaut : ordre chronologique)")
    parser.add_argument("--seed", type=int, default=None,
                         help="Graine aléatoire pour --shuffle (pour un ordre reproductible)")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date() if args.end else date.today()

    if args.calibrate:
        print(f"Calibration à partir de : {args.calibrate}")
        slope, intercept, known_ranges = calibrate_from_file(args.calibrate)
        print(f"  -> slope={slope:.3f}  intercept={intercept:.2f}  mois connus={len(known_ranges)}")
    else:
        print("Aucune calibration fournie, utilisation des valeurs par défaut "
              "(calculées sur le jeu de données de référence).")
        slope, intercept, known_ranges = DEFAULT_SLOPE, DEFAULT_INTERCEPT, {}

    print(f"Génération de la wordlist du {start} au {end} ...")
    urls = list(generate_wordlist(start, end, slope, intercept, known_ranges,
                                   args.margin_known, args.margin_unknown))

    if args.shuffle:
        if args.seed is not None:
            random.seed(args.seed)
        random.shuffle(urls)
        print("[info] URLs mélangées (--shuffle activé).", file=sys.stderr)

    count = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for url in urls:
            f.write(url + "\n")
            count += 1

    print(f"\nTerminé : {count} URLs candidates écrites dans {args.out}")
    print("Étape suivante : vérifier ces URLs avec check_urls.py")
    print(f"  python3 check_urls.py {args.out}")


if __name__ == "__main__":
    main()