# ScrapperBF

Scraper + pipeline d'extraction structurée des **marchés publics du Burkina Faso** (Quotidiens DGCMEF / dgcmef.gov.bf).

Le projet transforme les PDF du « Quotidien » (via OCR/MinerU → Markdown) en données JSON structurées (avis + résultats), avec validation, dashboard HTML et reprise sur échec pour backfill.

## Architecture (4 couches)

- **Layer 1 — `layer1_extract.py`** : parse le Markdown (titres, textes, tableaux), nettoie bruit (footers `N°...`, `www...`, `Sommaire`), produit 1 page virtuelle JSON.
- **Layer 2 — `new_logic/layer2_segment.py`** : segmente en unités métier (`AVIS` / `RESULTATS`), détecte catégories, lots, pages début/fin, `flat_text`.
- **Layer 3 — `layer3_extract_llm.py`** : extraction sémantique via LLM + schéma configurable. Schéma dans `schema_config.yaml` (aucun changement de code pour ajouter un champ). Rotation multi-provider : OpenRouter Xiaomi → Gemini → OpenRouter Mistral, avec backoff exponentiel.
- **Layer 4 — `pipeline.py`** : validation minimale (champs `required` du YAML) + fallback, tracking SQLite (`ingestion.db`) pour exécution hebdo + backfill idempotent (`numero` → `ok` / `echec_partiel`), `reprocess_failures()` pour rejouer uniquement les échecs.

Outils annexes :

| Script | Rôle |
|---|---|
| `generate_wordlist.py` | Génère URLs candidates des PDF par régression linéaire (n° édition ~ +21.7/mois) × patterns de nommage × domaines |
| `check_url.py` | Vérifie status HTTP (HEAD → fallback GET, retries, 10 workers), rapport CSV détaillé + résumé par pattern |
| `new_logic/pdf_to_md.py`, `split_pdf.py`, `tab_pdf_detector.py`, `detection2page.py`, `doublon_detection.py`, `separate.py`, `extract_single_page.py` | Pré-traitement PDF → MD, split, détection tableaux/doublons |
| `viewdashboard.py` + `dashboard.html` | Explorateur HTML des avis (sidebar + recherche) |
| `extract_page_2.py`, `extract_pdf.py` | Extraction ponctuelle PDF |

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env  # puis renseigner vos clés
```

Variables requises dans `.env` (ou environnement) :
- `OPENROUTER_API_KEY` — pour Xiaomi + Mistral
- `GEMINI_API_KEY` — repli Gemini
- `OPENROUTER_XIAOMI_MODEL` (défaut `xiaomi/mimo-v2.5`)
- `OPENROUTER_MISTRAL_MODEL` (défaut `mistralai/mistral-large`)

## Usage

```bash
# Pipeline complet sur un Markdown
python pipeline.py quotidien_4466.md N4466
# → output/N4466.json + ingestion.db

# Couches isolées
python layer1_extract.py
python new_logic/layer2_segment.py
python layer3_extract_llm.py

# Wordlist + vérification URLs
python generate_wordlist.py --calibrate urls_quotidien.txt --out wordlist.txt
python check_url.py wordlist.txt

# Dashboard
python viewdashboard.py
```

## Configuration métier

`schema_config.yaml` définit les champs `RESULTATS` et `AVIS` (types, descriptions, `required`). Ajouter un champ = éditer le YAML uniquement. Le JSON Schema envoyé au LLM est généré dynamiquement (`build_json_schema`).

## Données locales (non versionnées)

Les PDF, Markdown sources, `wordlist.txt`, `urls_*.txt`, `*_output.json`, `output/`, `*.db` sont ignorés via `.gitignore` (voir `.env.example`). Ne committer jamais de clé API — `layer3_extract_llm.py` lit uniquement l'environnement.

## Sécurité

Anciennes clés en dur supprimées de `layer3_extract_llm.py`. Si elles ont déjà été pushées ailleurs, les révoquer sur OpenRouter / Google AI Studio et en générer de nouvelles.
