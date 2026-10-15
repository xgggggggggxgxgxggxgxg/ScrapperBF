# ScrapperBF

Scraper + pipeline d'extraction structurée des **marchés publics du Burkina Faso** (Quotidiens DGCMEF / dgcmef.gov.bf).

Le projet transforme les PDF du « Quotidien » (via OCR/MinerU → Markdown) en données JSON structurées (avis + résultats), avec validation, dashboard HTML et reprise sur échec pour backfill.

## Architecture (4 couches)

- **Layer 1 — `layer1_extract.py`** : parse le Markdown (titres, textes, tableaux), nettoie bruit (footers `N°...`, `www...`, `Sommaire`), produit 1 page virtuelle JSON.
- **Layer 2 — `new_logic/layer2_segment.py`** : segmente en unités métier (`AVIS` / `RESULTATS`), détecte catégories, lots, pages début/fin, `flat_text`.
- **Layer 3 — `layer3_extract_llm.py`** : première extraction sémantique via LLM + schéma configurable. Schéma dans `schema_config.yaml` (aucun changement de code pour ajouter un champ). Rotation multi-provider : OpenRouter Xiaomi → Gemini → OpenRouter Mistral, avec backoff exponentiel (`call_with_rotation`, réutilisée par la Couche 4).
- **Layer 4 — `layer4_review.py`** : **auto-analyse + auto-complétion**. On redonne au LLM le *texte intégral* de l'annonce découpée **et** les valeurs déjà extraites ; il revoit **toutes** les variables (confirme / corrige / complète) et ne laisse vide que ce qui n'existe **réellement pas** dans le texte. Sortie enrichie d'une piste d'audit par champ : `_analyse` (`{champ, statut, confiance, justification}`, statut ∈ `confirme|corrige|complete|absent`), `_champs_absents` (vides assumés) et `_champs_a_verifier` (vides anormaux → relecture humaine). Agnostique de la 1re passe (regex, LLM ou OCR). Désactivable via `ENABLE_REVIEW=0`.
  - **Score d'exactitude + raisons** : `compute_exactitude()` calcule un score déterministe `[0..100]` par annonce. Dès qu'il est **< 100 %**, les **raisons** de l'écart sont listées champ par champ (`_raisons_ecart` : `{champ, statut, confiance, fiabilite, raison}`). La confiance auto-déclarée ne module qu'un champ déjà présent ; les vrais zéros viennent de faits vérifiables (champ requis absent, champ vide non résolu).
- **Le juge — `judge.py`** : **vérificateur de cohérence déterministe** (le rôle « contrôleur qualité » décrit pour un moteur de décision type Jev AI). Les contrôles croisés texte ↔ valeurs sont déterministes et quasi gratuits : ils tournent en **local**, pas dans un LLM. Après extraction, `judge_avis()` / `judge_resultats()` posent des questions fermées (pass/warn/fail/na) — complétude, **ratio caution/budget** (≈ 1–2 %), **montant plausible** (détecte un facteur 1000), **cohérence du nombre de lots** (annoncé vs extrait), **date limite** présente et lisible, **signature de clôture** (avis tronqué ?), **texte lisible** (corruption police) — les pondèrent en une **confiance [0..1]** et un **verdict** (`valide` ≥ 0,9 / `a_verifier` / `rejete`). Confiance < seuil → **file de revue humaine**. Calibré sur 86 avis réels : 77 validés, 9 à vérifier, 0 rejeté. Le modèle de décision réutilise ses primitives (`garbling_score`, montant implausible) pour router vers l'API.
- **Boucle de rétroaction — `feedback_store.py`** : chaque écart (< 100 %) est enregistré, puis **réinjecté** dans le prompt de révision des traitements suivants (`feedback_hint`) sous forme de « erreurs récurrentes à éviter ». Le modèle voit explicitement ses fautes passées → il s'améliore d'un run à l'autre. `summary()` donne un classement des champs les plus fautifs (tableau de bord qualité).
- **Orchestration — `pipeline.py`** : enchaîne Couches 1→4, injecte le `feedback_hint`, enregistre les écarts, validation minimale (champs `required` du YAML) + fallback, tracking SQLite (`ingestion.db`) pour exécution hebdo + backfill idempotent (`numero` → `ok` / `echec_partiel`), `reprocess_failures()` pour rejouer uniquement les échecs.

Extraction déterministe directe (sans LLM ni OCR externe) :

- **`pdf_avis_extract.py`** : lit un PDF de Quotidien avec `pdfplumber`, localise la section AVIS, découpe chaque avis sur son en-tête de type et extrait par regex les champs lisibles (émetteur, objet, type, référence, financement, montant, délai, date limite) + la provenance (bulletin, page). C'est une « première passe » : les champs non trouvés sont signalés `_champs_a_verifier` (jamais inventés), et la couche 4 peut ensuite compléter. `enrich_coverage()` ajoute un indicateur de complétude. Le montant prévisionnel est **classé en `{htva, ttc}`** quand les deux valeurs sont présentes (`parse_montant`), et la date limite gère les tournures « au plus tard le… », « avant le… JJ/MM à HH heure », etc. (couverture date 73 % → 99 %).
- **`decision_model.py`** : la **couche de décision** entre la 1re passe et l'API. Elle regarde le texte de l'annonce **et** les valeurs extraites, et décide s'il faut **renvoyer à l'API** pour une meilleure extraction, en pointant les champs fautifs : champ requis vide, champ vide **alors que** le texte contient un indice (`parvenir`, `avant le`…), valeur **à classer** (montant brut HTVA+TTC à répartir en `htva`/`ttc`), queue parasite (« …TTC **Cet** »), valeur trop longue. `refine_if_needed()` enchaîne décision → Couche 4 **uniquement** si besoin (sur les 5 bulletins : 22/86 annonces renvoyées à l'API, 64 jugées propres), avec une consigne ciblée (`hint_pour_llm`) pour que le modèle classe précisément les valeurs multiples.
- **`search_avis.py`** : recherche d'avis par mots-clés, insensible à la casse **et aux accents**, multi mots-clés en ET/OU, pondérée par champ (objet > financement), avec score, champs matchés et extrait. `simulate()` rejoue une batterie de recherches types (`--simulate`) pour vérifier la pertinence sur un vrai jeu de données. Même logique de normalisation que la recherche de la plateforme.

Section RÉSULTATS (tableaux de dépouillement) :

- **`pdf_resultats_extract.py`** : segmente la section RÉSULTATS. Deux macro-sections (*Ministères/institutions/MOD*, puis *Régions/collectivités*). Chaque émetteur est ouvert par une **barre noire** pleine largeur (texte blanc) détectée via `pdfplumber` ; une unité = tout ce qui va d'une barre noire à la suivante (même sur plusieurs pages). Les **lots sont repérés par le TEXTE** (`find_lot_markers`, « Lot N : … ») — plus fiable que la bande de couleur (facultative), comptée seulement en secours. La macro-section bascule sur *Régions* dès le premier émetteur « RÉGION/COMMUNE/… » (les en-têtes décoratifs ne sont pas du texte sélectionnable). Produit `{emetteur, macro_section, page_debut/fin, nb_lots_detectes, lots, flat_text}`.
- **`resultats_llm.py`** : **instructions à l'IA** pour restructurer ces tableaux multi-colonnes (Soumissionnaire · Montant lu HTVA/TTC min/max · Montant corrigé HTVA/TTC min/max · Observations), lot par lot, avec attributaire provisoire. Le prompt système décrit la structure exacte, et on passe au modèle l'indice déterministe `nb_lots_detectes`. `extract_resultats_unit()` fait un **appel unique** si l'unité tient en un prompt, sinon découpe + fusionne (voir ci-dessous). `python resultats_llm.py <pdf>` affiche le prompt exact, sans appel réseau.
- **`resultats_chunker.py`** : découpe les **unités géantes** (ex. 4503bis : 1 émetteur, 94 pages, 57 lots) en préservant **l'intégrité** : on coupe **aux frontières de lots** (jamais au milieu d'un lot), chaque chunk **rappelle le contexte** (émetteur + préambule), un lot trop grand est découpé avec **chevauchement** (marqué « SUITE du Lot N »), et `merge_lot_results()` **fusionne par lot en dédupliquant** les soumissionnaires par nom (le chevauchement ne double rien) et réconcilie attributaire/infructueux.

Outils annexes :

| Script | Rôle |
|---|---|
| `generate_wordlist.py` | Génère URLs candidates des PDF par régression linéaire (n° édition ~ +21.7/mois) × patterns de nommage × domaines |
| `check_url.py` | Vérifie status HTTP (HEAD → fallback GET, retries, 10 workers), rapport CSV détaillé + résumé par pattern |
| `new_logic/pdf_to_md.py`, `split_pdf.py`, `tab_pdf_detector.py`, `detection2page.py`, `doublon_detection.py`, `separate.py`, `extract_single_page.py` | Pré-traitement PDF → MD, split, détection tableaux/doublons |
| `web/portail.html` | **Site pro** (onglets Avis / Résultats / Qualité, filtre par bulletin, recherche, thème clair/sombre). Ouvrir dans un navigateur et charger un `output/<numero>.json` (bouton « Charger un .json » ou « Coller »). |
| `viewdashboard.py` + `dashboard.html` | Ancien explorateur HTML local des avis |
| `extract_page_2.py`, `extract_pdf.py` | Extraction ponctuelle PDF |

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env  # puis renseigner vos clés
```

### Connecter vos API LLM (chez vous)

Le `.env` est **chargé automatiquement** (`config.py`, sans dépendance). Un provider
n'est activé que si sa clé est présente — vous pouvez n'en configurer qu'un. L'ordre
du fichier = ordre d'essai (rotation / repli). Trois types de providers :

- **OpenRouter** : `OPENROUTER_API_KEY` (+ `OPENROUTER_MODELS=modeleA,modeleB` essayés dans l'ordre).
- **Google Gemini** : `GEMINI_API_KEY` (+ `GEMINI_MODEL`, défaut `gemini-2.0-flash`).
- **Tout endpoint compatible OpenAI** (modèle **local** Ollama/LM Studio/vLLM, ou passerelle Groq/Together…) : `OPENAI_COMPAT_BASE_URL` (ex. `http://localhost:11434/v1`) + `OPENAI_COMPAT_API_KEY` (vide possible en local) + `OPENAI_COMPAT_MODEL`.

```bash
python check_llm.py          # liste les providers configurés (hors-ligne)
python check_llm.py --ping   # teste réellement la connexion à chacun (chez vous)
```

## Usage

```bash
# Chaîne complète directement sur un PDF (AVIS + RÉSULTATS)
python pipeline.py "Quotidien_N_4503.pdf"
#   AVIS      : regex (1re passe) → modèle de décision → API (si clé configurée)
#   RÉSULTATS : segmentation (cadres noirs) → chunk → API → fusion
# → output/N4503.json = {meta, AVIS, RESULTATS} ; 100 % déterministe sans clé.

# Ancienne chaîne AVIS depuis un Markdown
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

# RÉSULTATS : segmentation par cadres noirs + aperçu du prompt LLM (hors-ligne)
python pdf_resultats_extract.py Quotidien_N_4503.pdf
python resultats_llm.py Quotidien_N_4503.pdf

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
