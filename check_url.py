#!/usr/bin/env python3
"""
Script de vérification des status codes HTTP des URLs de PDF "Quotidien"
(dgcmef.gov.bf), du 01/01/2022 à aujourd'hui.

Fonctionnement :
1. Lit la liste d'URLs depuis un fichier texte (une URL par ligne).
2. Filtre les URLs dont la date (YYYY-MM dans le chemin /sites/default/files/YYYY-MM/...)
   est postérieure ou égale au 01/01/2022.
3. Classe chaque URL selon son "pattern" de nommage (ex: "Quotidien N°####.pdf",
   "Quotidien N°####_#.pdf", "Quotidien N°#### (bis).pdf", etc.) déjà identifiés
   à partir du fichier fourni.
4. Vérifie le status code HTTP de chaque URL (avec retries, gestion des timeouts,
   fallback GET si HEAD échoue/est bloqué).
5. Exporte un rapport CSV détaillé + un résumé par pattern et par statut.

Usage:
    python3 check_urls.py urls_quotidien.txt

Options utiles (voir en bas du fichier / variables de config) :
    - MAX_WORKERS : nombre de requêtes en parallèle
    - TIMEOUT     : timeout par requête (secondes)
    - START_DATE  : date de début du filtre (par défaut 2022-01-01)
"""

import re
import sys
import csv
import time
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, unquote

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ----------------------------------------------------------------------------
# CONFIGURATION
# ----------------------------------------------------------------------------
START_DATE = datetime(2022, 1, 1)   # 01/01/2022
END_DATE = datetime.today()         # aujourd'hui
MAX_WORKERS = 10                    # requêtes en parallèle
TIMEOUT = 20                        # secondes
MAX_RETRIES = 2
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 URLChecker/1.0"
)

OUTPUT_DETAIL_CSV = "rapport_status_urls.csv"
OUTPUT_SUMMARY_CSV = "resume_par_pattern.csv"


# ----------------------------------------------------------------------------
# 1. IDENTIFICATION DES PATTERNS DE NOMMAGE
# ----------------------------------------------------------------------------
# Chaque pattern est défini par une regex appliquée au nom de fichier (partie
# après le dernier "/" de l'URL, décodée en UTF-8). L'ordre compte : le premier
# pattern qui matche est retenu. Ces patterns ont été extraits par analyse du
# fichier urls_quotidien.txt (généralisation des chiffres en "#").
FILENAME_PATTERNS = [
    ("standard",                 r'^Quotidien N°\d+\.pdf$'),
    ("standard_suffixe_num",     r'^Quotidien N°\d+_\d+\.pdf$'),
    ("standard_minuscule",       r'^Quotidien n°\d+\.pdf$'),
    ("bis_espace",                r'^Quotidien N°\d+ \(bis\)\.pdf$'),
    ("double_numero_tiret",       r'^Quotidien N°\d+-\d+\.pdf$'),
    ("minuscule_suffixe_num",     r'^Quotidien n°\d+_\d+\.pdf$'),
    ("bis_espace_C",              r'^Quotidien N°\d+ \(bis\)_C\.pdf$'),
    ("bis_sans_espace",           r'^Quotidien N°\d+\(bis\)\.pdf$'),
    ("double_numero_tiret_suffixe",r'^Quotidien N°\d+-\d+_\d+\.pdf$'),
    ("bis_compresser",            r'^Quotidien N°\d+ \(bis\)_Compresser.*\.pdf$'),
    ("bis_espace_suffixe",        r'^Quotidien N°\d+ \(bis\)_\d+\.pdf$'),
    ("minuscule_double_tiret",    r'^Quotidien n°\d+-\d+\.pdf$'),
    ("bis_tiret_C",               r'^Quotidien N°\d+ \(bis\)-C\.pdf$'),
    ("prefixe_underscore",        r'^_Quotidien [Nn]°\d+\.pdf$'),
    ("minuscule_tiret_c",         r'^Quotidien n°-\d+-c\.pdf$'),
    ("underscore_bis",            r'^Quotidien_N°\d+_\(bis\).*\.pdf$'),
    ("majuscule_underscore_bis",  r'^QUOTIDIEN_N°\d+_\(bis\)\.pdf$'),
    ("minuscule_tiret_c_v2",      r'^Quotidien n-\d+-c\.pdf$'),
    ("word_prefixe",              r'^Quotidien_word N°\d+.*\.pdf$'),
    ("double_N",                  r'^Quotidien N°N°\d+.*\.pdf$'),
    ("majuscule_espace",          r'^QUOTIDIEN N°\d+_\d+\.pdf$'),
    ("bis_sans_espace_suffixe",   r'^Quotidien N°\d+\(bis\)_\d+\.pdf$'),
    ("triple_suffixe",            r'^Quotidien N°\d+_\d+_\d+\.pdf$'),
    ("bis_mot_complet",           r'^Quotidien N°\d+ bis(_Compresser)?\.pdf$'),
    ("point_double",              r'^Quotidien N°\d+\.\.pdf$'),
    ("avec_compression_copie",    r'^Quotidien N°\d+-avec compression - Copie\.pdf$'),
    ("suffixe_X",                 r'^Quotidien N°\d+-X\.pdf$'),
    # --- Patterns de repli plus génériques (numéros multiples, espaces
    # irrégulières, combinaisons bis/C/Compresser non couvertes ci-dessus) ---
    ("multi_numeros",             r'^Quotidien [Nn]°\d+([ _-]+\d+){1,3}.*\.pdf$'),
    ("numero_espace_suffixe",     r'^Quotidien [Nn]°\d+ +\.pdf$'),
    ("numero_espace_underscore",  r'^Quotidien [Nn]°\d+ +_\d+\.pdf$'),
    ("bis_variante_generique",    r'^Quotidien [Nn]°\d+.*\(bis\).*\.pdf$'),
    ("prefixe_underscore_multi",  r'^_Quotidien [Nn]°\d+([ _-]+\d+)*.*\.pdf$'),
    ("generique_quotidien",       r'^Quotidien [Nn]°\d+.*\.pdf$'),
]
FILENAME_PATTERNS_COMPILED = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in FILENAME_PATTERNS]

# Pattern du dossier de date dans le chemin: /sites/default/files/YYYY-MM/
DATE_DIR_RE = re.compile(r'/sites/default/files/(\d{4})-(\d{2})/')


def classify_filename(filename: str) -> str:
    """Retourne le nom du pattern reconnu, ou 'autre' si aucun ne correspond."""
    for name, regex in FILENAME_PATTERNS_COMPILED:
        if regex.match(filename):
            return name
    return "autre_non_classe"


def extract_date(url: str):
    """Extrait (année, mois) du chemin de l'URL. Retourne None si absent."""
    m = DATE_DIR_RE.search(url)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    try:
        return datetime(year, month, 1)
    except ValueError:
        return None


def normalize_domain_variant(url: str) -> str:
    """Identifie la variante de domaine utilisée (http/https, avec/sans www)."""
    parsed = urlparse(url)
    scheme = parsed.scheme
    host = parsed.netloc
    return f"{scheme}://{host}"


# ----------------------------------------------------------------------------
# 2. LECTURE + FILTRAGE DES URLs
# ----------------------------------------------------------------------------
def load_and_filter_urls(path: str):
    raw_lines = Path(path).read_text(encoding="utf-8").splitlines()
    urls = [l.strip() for l in raw_lines if l.strip()]

    kept = []
    skipped_no_date = []
    skipped_out_of_range = []

    for url in urls:
        d = extract_date(url)
        if d is None:
            skipped_no_date.append(url)
            continue
        if d < datetime(START_DATE.year, START_DATE.month, 1) or d > END_DATE:
            skipped_out_of_range.append(url)
            continue
        kept.append(url)

    return kept, skipped_no_date, skipped_out_of_range


# ----------------------------------------------------------------------------
# 3. VERIFICATION HTTP
# ----------------------------------------------------------------------------
def build_session():
    session = requests.Session()
    retries = Retry(
        total=MAX_RETRIES,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET"],
    )
    adapter = HTTPAdapter(max_retries=retries, pool_maxsize=MAX_WORKERS * 2)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    return session


def check_url(session: requests.Session, url: str) -> dict:
    """Vérifie une URL : essaie HEAD puis, si échec/statut suspect, GET en streaming."""
    result = {
        "url": url,
        "status_code": None,
        "final_url": url,
        "method": "HEAD",
        "error": "",
        "redirected": False,
    }
    try:
        r = session.head(url, timeout=TIMEOUT, allow_redirects=True)
        # Certains serveurs ne supportent pas HEAD correctement (405, 403, ou 200
        # generique) -> on retente en GET pour confirmer.
        if r.status_code in (405, 403, 501) or r.status_code >= 400:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            result["method"] = "GET"
        result["status_code"] = r.status_code
        result["final_url"] = r.url
        result["redirected"] = (r.url != url)
    except requests.exceptions.RequestException as e:
        # Fallback : tente un GET si le HEAD a levé une exception réseau
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True, stream=True)
            result["status_code"] = r.status_code
            result["final_url"] = r.url
            result["method"] = "GET"
            result["redirected"] = (r.url != url)
        except requests.exceptions.RequestException as e2:
            result["status_code"] = "ERREUR"
            result["error"] = str(e2)
    return result


def check_all(urls, max_workers=MAX_WORKERS):
    session = build_session()
    results = []
    total = len(urls)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_url, session, u): u for u in urls}
        done = 0
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            done += 1
            if done % 25 == 0 or done == total:
                print(f"  ... {done}/{total} URLs vérifiées")
    return results


# ----------------------------------------------------------------------------
# 4. RAPPORT
# ----------------------------------------------------------------------------
def build_report(urls_checked):
    detail_rows = []
    for res in urls_checked:
        url = res["url"]
        filename = unquote(url.split("/")[-1])
        d = extract_date(url)
        pattern = classify_filename(filename)
        domain_variant = normalize_domain_variant(url)
        detail_rows.append({
            "url": url,
            "status_code": res["status_code"],
            "methode_verif": res["method"],
            "url_finale": res["final_url"],
            "redirige": res["redirected"],
            "erreur": res["error"],
            "annee_mois": d.strftime("%Y-%m") if d else "",
            "pattern_nom_fichier": pattern,
            "variante_domaine": domain_variant,
            "nom_fichier": filename,
        })
    return detail_rows


def write_detail_csv(rows, path):
    fieldnames = [
        "url", "status_code", "methode_verif", "url_finale", "redirige",
        "erreur", "annee_mois", "pattern_nom_fichier", "variante_domaine", "nom_fichier",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary_csv(rows, path):
    from collections import defaultdict
    summary = defaultdict(lambda: defaultdict(int))
    for r in rows:
        summary[r["pattern_nom_fichier"]][r["status_code"]] += 1

    all_statuses = sorted({r["status_code"] for r in rows}, key=lambda x: str(x))

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pattern_nom_fichier", "total"] + [f"status_{s}" for s in all_statuses])
        for pattern, counts in sorted(summary.items(), key=lambda kv: -sum(kv[1].values())):
            total = sum(counts.values())
            row = [pattern, total] + [counts.get(s, 0) for s in all_statuses]
            writer.writerow(row)


def print_summary(rows, skipped_no_date, skipped_out_of_range):
    from collections import Counter
    status_counter = Counter(r["status_code"] for r in rows)
    pattern_counter = Counter(r["pattern_nom_fichier"] for r in rows)

    print("\n" + "=" * 70)
    print("RESUME GLOBAL")
    print("=" * 70)
    print(f"Total URLs analysées (dans la période 01/01/2022 -> aujourd'hui) : {len(rows)}")
    print(f"URLs ignorées (date non détectée dans le chemin)                : {len(skipped_no_date)}")
    print(f"URLs ignorées (hors période)                                    : {len(skipped_out_of_range)}")

    print("\n--- Répartition par status code ---")
    for status, count in status_counter.most_common():
        print(f"  {status} : {count}")

    print("\n--- Répartition par pattern de nom de fichier ---")
    for pattern, count in pattern_counter.most_common():
        # sous-détail des statuts en erreur pour ce pattern
        broken = [r for r in rows if r["pattern_nom_fichier"] == pattern and r["status_code"] not in (200,)]
        broken_str = f" ({len(broken)} en erreur/non-200)" if broken else ""
        print(f"  {pattern} : {count}{broken_str}")

    broken_total = [r for r in rows if r["status_code"] != 200]
    print(f"\nTotal URLs problématiques (status != 200) : {len(broken_total)}")
    if broken_total:
        print("Exemples (max 15) :")
        for r in broken_total[:15]:
            print(f"  [{r['status_code']}] {r['url']}")


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 check_urls.py <fichier_urls.txt>")
        sys.exit(1)

    input_path = sys.argv[1]
    print(f"Lecture du fichier : {input_path}")
    urls, skipped_no_date, skipped_out_of_range = load_and_filter_urls(input_path)
    print(f"URLs retenues (>= 01/01/2022) : {len(urls)}")
    if skipped_no_date:
        print(f"URLs sans date détectée (ignorées) : {len(skipped_no_date)}")
    if skipped_out_of_range:
        print(f"URLs hors période (ignorées) : {len(skipped_out_of_range)}")

    print(f"\nVérification des status codes ({MAX_WORKERS} en parallèle, timeout {TIMEOUT}s)...")
    t0 = time.time()
    checked = check_all(urls)
    elapsed = time.time() - t0
    print(f"Terminé en {elapsed:.1f}s")

    rows = build_report(checked)
    write_detail_csv(rows, OUTPUT_DETAIL_CSV)
    write_summary_csv(rows, OUTPUT_SUMMARY_CSV)
    print(f"\nRapport détaillé  : {OUTPUT_DETAIL_CSV}")
    print(f"Résumé par pattern : {OUTPUT_SUMMARY_CSV}")

    print_summary(rows, skipped_no_date, skipped_out_of_range)


if __name__ == "__main__":
    main()