# ScrapperBF

Scraper + pipeline d'extraction structurée des **marchés publics du Burkina Faso** (Quotidiens DGCMEF / dgcmef.gov.bf).

Le projet transforme les PDF du « Quotidien » (via OCR/MinerU → Markdown) en données JSON structurées (avis + résultats), avec validation, dashboard HTML et reprise sur échec pour backfill.

## Architecture (4 couches)

- **Layer 1 — `layer1_extract.py`** : parse le Markdown (titres, textes, tableaux), nettoie bruit (footers `N°...`, `www...`, `Sommaire`), produit 1 page virtuelle JSON.
- **Layer 2 — `new_logic/layer2_segment.py`** : segmente en unités métier (`AVIS` / `RESULTATS`), détecte catégories, lots, pages début/fin, `flat_text`.
- **Layer 3 — `layer3_extract_llm.py`** : première extraction sémantique via LLM + schéma configurable. Schéma dans `schema_config.yaml` (aucun changement de code pour ajouter un champ). Rotation multi-provider : OpenRouter Xiaomi → Gemini → OpenRouter Mistral, avec backoff exponentiel (`call_with_rotation`, réutilisée par la Couche 4).
- **Layer 4 — `layer4_review.py`** : **auto-analyse + auto-complétion**. On redonne au LLM le *texte intégral* de l'annonce découpée **et** les valeurs déjà extraites ; il revoit **toutes** les variables (confirme / corrige / complète) et ne laisse vide que ce qui n'existe **réellement pas** dans le texte. Sortie enrichie d'une piste d'audit par champ : `_analyse` (`{champ, statut, confiance, justification}`, statut ∈ `confirme|corrige|complete|absent`), `_champs_absents` (vides assumés) et `_champs_a_verifier` (vides anormaux → relecture humaine). Agnostique de la 1re passe (regex, LLM ou OCR). Désactivable via `ENABLE_REVIEW=0`.
  - **Score d'exactitude + raisons** : `compute_exactitude()` calcule un score déterministe `[0..100]` par annonce. Dès qu'il est **< 100 %**, les **raisons** de l'écart sont listées champ par champ (`_raisons_ecart` : `{champ, statut, confiance, fiabilite, raison}`). La confiance auto-déclarée ne module qu'un champ déjà présent ; les vrais zéros viennent de faits vérifiables (champ requis absent, champ vide non résolu).
- **Boucle de rétroaction — `feedback_store.py`** : chaque écart (< 100 %) est enregistré, puis **réinjecté** dans le prompt de révision des traitements suivants (`feedback_hint`) sous forme de « erreurs récurrentes à éviter ». Le modèle voit explicitement ses fautes passées → il s'améliore d'un run à l'autre. `summary()` donne un classement des champs les plus fautifs (tableau de bord qualité).
- **Orchestration — `pipeline.py`** : enchaîne Couches 1→4, injecte le `feedback_hint`, enregistre les écarts, validation minimale (champs `required` du YAML) + fallback, tracking SQLite (`ingestion.db`) pour exécution hebdo + backfill idempotent (`numero` → `ok` / `echec_partiel`), `reprocess_failures()` pour rejouer uniquement les échecs.

Extraction déterministe directe (sans LLM ni OCR externe) :

- **`pdf_avis_extract.py`** : lit un PDF de Quotidien avec `pdfplumber`, localise la section AVIS, découpe chaque avis sur son en-tête de type et extrait par regex les champs lisibles (émetteur, objet, type, référence, financement, montant, délai, date limite) + la provenance (bulletin, page). C'est une « première passe » : les champs non trouvés sont signalés `_champs_a_verifier` (jamais inventés), et la couche 4 peut ensuite compléter. `enrich_coverage()` ajoute un indicateur de complétude.
- **`search_avis.py`** : recherche d'avis par mots-clés, insensible à la casse **et aux accents**, multi mots-clés en ET/OU, pondérée par champ (objet > financement), avec score, champs matchés et extrait. `simulate()` rejoue une batterie de recherches types (`--simulate`) pour vérifier la pertinence sur un vrai jeu de données. Même logique de normalisation que la recherche de la plateforme.

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

# Désactiver l'auto-complétion (run purement 1re passe)
ENABLE_REVIEW=0 python pipeline.py quotidien_4466.md N4466

# Couches isolées
python layer1_extract.py
python new_logic/layer2_segment.py
python layer3_extract_llm.py

# Aperçu HORS-LIGNE du prompt de révision (aucun appel réseau)
python layer4_review.py annonce.txt AVIS

# Classement des champs les plus souvent en écart (qualité / amélioration)
python feedback_store.py

# Extraction déterministe des AVIS directement depuis des PDF (sans LLM)
python pdf_avis_extract.py Quotidien_N_4500.pdf Quotidien_N_4502.pdf -o dataset_avis.json

# Recherche par mots-clés + simulation d'un lecteur (sur le JSON produit)
python search_avis.py dataset_avis.json "matériel informatique"
python search_avis.py dataset_avis.json --simulate

# Tests hors-ligne (providers LLM injectés, sans réseau ni clé)
pip install pytest && python -m pytest tests/ -q

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
