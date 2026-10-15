# ScrapperBF

Scraper + pipeline d'extraction structurée des **marchés publics du Burkina Faso**
(Quotidiens DGCMEF / dgcmef.gov.bf), avec une **plateforme de contrôle qualité** :
les annonces sont triées par leur titre, notées, et les problèmes sont présentés à
un opérateur humain avec leurs références de page.

## Les deux moitiés du projet

| | Rôle |
|---|---|
| **Pipeline** (`pipeline.py`, `layer*.py`) | PDF/Markdown → annonces JSON structurées |
| **Plateforme QA** (`scrapperbf/`) | trie, vérifie, note, alerte, et renvoie les corrections humaines vers l'IA |

## Chaîne de traitement

```
PDF  ──scrapperbf.ocr──▶  Markdown  ──core.pages──▶  pages numérotées
                                                          │
                                        core.titles ◀── core.segment
                                     (nature de l'annonce)   │
                                                          ▼
                                   core.priority  (champs attendus et leur poids)
                                                          │
                              layer3_extract_llm ─────────┤
                                                          ▼
                             qa.rules + qa.scoring (validité, plausibilité, ancrage)
                             qa.duplicates + qa.multipage (doubles, continuité)
                                                          ▼
                                      qa.report ──▶ rapport JSON + dashboard HTML
                                                          │
                                     feedback.store ◀── corrections de l'opérateur
                                            │
                                            └──▶ consignes réinjectées dans le prompt
```

## Principe du contrôle qualité

Le **titre** est le discriminant principal : il est court, mis en forme par la
rédaction du bulletin, et survit mieux à l'OCR qu'un tableau. De lui on déduit la
nature de l'annonce, et de la nature les **champs attendus avec leur priorité** —
une demande de prix sans date limite de dépôt est inutilisable, un horaire de
retrait de dossier manquant est un détail.

Chaque annonce reçoit trois notes, gardées séparées parce qu'elles n'ont pas le
même remède :

| Note | Question | Remède si basse |
|---|---|---|
| **Complétude** | les champs attendus sont-ils remplis ? | revoir la segmentation ou le schéma |
| **Exactitude** | les valeurs sont-elles valides, plausibles, **présentes dans la page** ? | prompt d'extraction, ou relecture humaine |
| **Structure** | a-t-on isolé et classé la bonne annonce ? | titres, découpage, qualité OCR |

L'**ancrage** (*grounding*) est le contrôle le plus important : les chiffres d'un
montant, d'une référence ou d'une date doivent exister dans le texte de la page.
C'est le seul contrôle qui attrape une hallucination du modèle.

Le verdict (`VALIDE` / `A_VERIFIER` / `SUSPECT` / `REJETE`) combine les trois, avec
un garde-fou : **un champ critique absent interdit « valide »**, quel que soit le
score moyen.

## Problèmes de document détectés

| Problème | Code d'alerte |
|---|---|
| Page scannée deux fois (similarité de n-grammes) | `PAGE_DUPLIQUEE` |
| Numéro de page répété, manquant, ou qui recule | `NUMEROTATION_PAGE_ANORMALE` |
| Annonce étalée sur plusieurs pages | `ANNONCE_MULTIPAGE` |
| Annonce coupée (ni signature ni ponctuation finale) | `ANNONCE_TRONQUEE` |
| Page rattachable à aucune annonce | `PAGE_ORPHELINE` |
| Annonce republiée / découpée en double | `ANNONCE_DUPLIQUEE` |
| Titre inconnu du catalogue, classement incertain | `TITRE_NON_CLASSE`, `CLASSEMENT_INCERTAIN` |
| Valeur inventée par le modèle | `CHAMP_NON_ANCRE` |
| Champ critique / important absent, invalide, douteux | `CHAMP_*` |
| Numérisation dégradée sur ces pages | `OCR_DEGRADE` |

Chaque alerte porte sa gravité, ses **références de page**, un message en français
et une piste d'action. La continuité inter-pages se décide sur des signaux de bord
de page (signature de clôture, phrase tronquée, numérotation réglementaire qui se
poursuit), avec une doctrine assumée : **en cas de doute, sous-fusionner** — mêler
deux annonces est un dégât silencieux, une annonce incomplète se voit au score.

## Extraction en cascade

L'ordre des marches est une décision économique autant que technique : chacune
ne s'exécute que si la précédente n'a pas suffi.

| Marche | Moyen | Coût |
|---|---|---|
| 1 | contrôle d'intégrité de la page | gratuit, et évite de dépenser pour rien |
| 2 | extraction par motifs (`extract/labels.py`) | gratuit, reproductible, justifiable |
| 3 | modèle de décision (`extract/cascade.py`) | gratuit : estime si les valeurs tiennent |
| 4 | relecture Light OCR de la seule page en cause | quelques secondes |
| 5 | appel LLM, limité aux champs manquants | facturé — paliers gratuits d'abord |
| 6 | juge de fiabilité (`qa/judge.py`) | gratuit ou un second appel |
| 7 | alerte administrateur | du temps humain, le plus précieux |

Mesuré sur les 98 annonces des bulletins réels : **78 % des informations
essentielles sont obtenues sans aucun appel d'API**, dont 100 % des émetteurs et
catégories (lus dans la maquette), 90 % des dates limites de dépôt, 86 % des
intitulés.

## Consignes données au modèle

`extraction_rules.yaml` est la seule chose à modifier pour changer ce que le
modèle comprend. Chaque champ y porte : sa définition, où le chercher, **comment
le distinguer de ses voisins**, la forme attendue, des exemples pris dans les
bulletins réels, les signes qui doivent faire baisser la confiance, et des
critères que le juge vérifie ensuite mécaniquement.

Le problème des valeurs multiples est traité en **faisant choisir plutôt que
chercher** : on relève d'abord tous les candidats par expression régulière
(toutes les dates, tous les téléphones, tous les montants), on les présente
numérotés avec leur contexte, et le modèle désigne celui qui convient en citant
le texte qui le justifie. Sur une annonce réelle, les candidats « dates » sont
l'échéance de dépôt, la date de parution du bulletin, et une date de pied de
page d'un autre numéro : sans cette liste, rien ne dit sur quoi le modèle s'est
fondé.

Le modèle rend pour chaque champ sa **valeur**, sa **confiance**, la
**citation** littérale qui la porte et les **candidats écartés**. Sans ces
quatre éléments, aucune vérification automatique n'est possible : on ne pourrait
que croire.

## Juge de fiabilité

Un modèle qui annonce « confiance 0.95 » ne prouve rien : il est aussi sûr de
lui quand il se trompe. Sa confiance ne pèse donc qu'un dixième de la note. Le
juge note chaque champ sur trois éléments vérifiables :

- **l'ancrage** (45 %) — la citation fournie existe-t-elle dans la page ? C'est
  le seul contrôle qui distingue une lecture d'une invention ;
- **les critères du référentiel** (30 %) — la date de dépôt précède l'ouverture
  des plis, le montant en lettres égale le montant en chiffres, un téléphone a
  huit chiffres, les lots sont consécutifs… ;
- **la forme** (15 %) — type, plausibilité.

Un critère qui échoue plafonne la note : une bonne moyenne ne doit pas effacer
un fait. Le score global est la moyenne pondérée par la priorité des champs,
exprimée en pourcentage. En dessous de **75 %**, l'annonce part en relecture
humaine.

Sur une extraction honnête le juge rend 99 % ; en remplaçant deux valeurs par
des valeurs inventées avec une confiance déclarée de 0,97 et 0,99, il rend 70 %
et nomme les deux champs fautifs à 21 % et 45 %.

## Fournisseurs LLM

Paliers gratuits d'abord, avec rotation : Groq, OpenRouter (modèles `:free`),
Gemini, Cerebras, Mistral, puis un palier payant seulement si une clé **et** un
modèle sont explicitement configurés. Chaque fournisseur a son propre quota, donc
la rotation sert autant de répartition de charge que de secours. Les identifiants
de modèles sont dans l'environnement, pas dans le code, car un modèle gratuit
change de nom sans préavis. Un fournisseur sans clé est ignoré, pas tenté.

```bash
export GROQ_API_KEY=...            # GROQ_MODEL pour changer de modèle
export OPENROUTER_API_KEY=...      # OPENROUTER_FREE_MODEL
export GEMINI_API_KEY=...          # GEMINI_MODEL
```

## Usage

```bash
pip install -r requirements.txt
cp .env.example .env    # puis renseigner vos clés

# 1. PDF → Markdown, en n'OCRisant que les pages qui en ont besoin
python -m scrapperbf ocr bulletin.pdf --out quotidien_4466.md --json ocr.json

# 2. Audit SANS appel LLM : structure du document seule (gratuit)
#    → écarter un PDF défectueux avant de dépenser des jetons
python -m scrapperbf audit quotidien_4466.md --numero N4466 \
    --json output/N4466_qa.json --dashboard output/N4466_dashboard.html

# 3. Pipeline complet : extraction LLM + contrôle qualité + dashboard
python pipeline.py quotidien_4466.md N4466
python pipeline.py quotidien_4466.md N4466 --reprocess-failures

# 4. L'opérateur relit dans le dashboard, exporte ses corrections, on les importe
python -m scrapperbf feedback import revue-N4466.json --reviewer ana
python -m scrapperbf feedback stats          # accord machine/humain, alertes calmées
python -m scrapperbf feedback prompt --kind DEMANDE_PRIX

# Voir le prompt et les candidats relevés, sans dépenser un appel
python -m scrapperbf prompt annonce.txt --champs date_limite_depot,date_ouverture_plis

# Index des annonces lu dans la maquette colorée du PDF
python -m scrapperbf bands bulletin.pdf --json index.json

# Diagnostic du classement par titre
python -m scrapperbf titles quotidien_4466.md --verbose --profiles
python -m scrapperbf fields DEMANDE_PRIX
```

`audit --fail-on-critical` rend un code de sortie 2 s'il reste une alerte
critique : exploitable en cron ou en CI pour bloquer un bulletin douteux.

## Dashboard de vérification

Fichier HTML autonome (aucun serveur, aucune ressource réseau), à ouvrir à côté du
PDF. Il permet de **trier**, **vérifier** et **corriger** :

- table triable, par défaut les annonces les moins fiables en haut ; filtres par
  verdict, nature, gravité d'alerte, « non relues » ;
- panneau par annonce : champs regroupés **par priorité** avec leur statut, alertes
  avec leurs pages, texte source de l'annonce ;
- saisie des corrections de champ, du verdict humain, et du jugement de chaque
  alerte (`problème confirmé` / `faux positif`), conservée dans le navigateur ;
- bouton d'export JSON, réimporté par `scrapperbf feedback import`.

## Boucle d'amélioration de l'IA

C'est ce qui fait que la relecture humaine ne meurt pas à l'écran :

1. l'opérateur corrige un champ dans le dashboard ;
2. `feedback.store` agrège les corrections par nature d'annonce et par champ ;
3. `prompt_supplement()` en fait des consignes ciblées (« ce champ a été corrigé
   N fois, voici ce qui avait été extrait à tort et la valeur correcte ») ;
4. `pipeline.py` les injecte dans le prompt d'extraction des bulletins suivants.

Les jugements d'alerte servent de même : un code massivement jugé faux positif voit
sa **gravité abaissée d'un cran** — pas supprimée, car un faux positif fréquent
reste parfois vrai. Sans cela, l'opérateur finit par ignorer toutes les alertes.

## OCR léger

Dans un PDF, **un titre n'est pas balisé** : il est seulement plus gros ou en
gras que le corps du texte. Comme tout le tri s'appuie sur les titres,
l'extraction les promeut en titres Markdown — corps de texte estimé par médiane
pondérée par la longueur des lignes, puis `#` / `##` selon le ratio de taille,
le gras ne servant de titre que sur une ligne courte (les avis mettent des
paragraphes entiers en gras). Le numéro de page imprimé, lui aussi en gras et
plus gros, n'est jamais promu : il doit rester reconnaissable comme pied de page.

`scrapperbf ocr` procède page par page, du moins cher au plus cher : il lit la
couche de texte intégrée, et ne lance l'OCR que sur les pages dont cette couche est
trop pauvre ou visiblement abîmée. Chaque page ressort avec sa provenance et une
confiance, consommées par le scoring : une annonce mal notée sur une page à faible
confiance OCR est un problème de numérisation, pas d'extraction.

Les moteurs (PyMuPDF, pytesseract) sont **optionnels** : sans eux, les PDF porteurs
d'une couche de texte restent traités et les pages qui auraient eu besoin d'OCR sont
signalées. Installation : `pip install -e '.[ocr]'` plus `tesseract-ocr-fra`.

## Structure

```
scrapperbf/
  core/    text.py pages.py titles.py priority.py segment.py
  qa/      rules.py scoring.py alerts.py duplicates.py multipage.py report.py
  ocr/     light_ocr.py
  feedback/store.py
  dashboard/build.py
  cli.py
tests/     365 tests, sans accès réseau
```

Couches historiques conservées : `layer1_extract.py` (Markdown → blocs),
`new_logic/layer2_segment.py` (segmentation d'origine), `layer3_extract_llm.py`
(extraction LLM, schéma piloté par `schema_config.yaml`, rotation multi-provider),
`pipeline.py` (couche 4 : validation, tracking SQLite, QA).

Outils annexes : `generate_wordlist.py` (URLs candidates par régression linéaire),
`check_url.py` (vérification HTTP), `new_logic/pdf_to_md.py`, `split_pdf.py`,
`tab_pdf_detector.py`, `detection2page.py`, `doublon_detection.py`,
`viewdashboard.py` (explorateur simple des avis).

## Configuration métier

`schema_config.yaml` définit **ce qui** est extrait (types, descriptions,
`required`) : ajouter un champ = éditer le YAML, le JSON Schema envoyé au LLM est
généré dynamiquement.

`scrapperbf/core/priority.py` définit **ce qui compte selon la nature de
l'annonce** — ce que le YAML ne peut pas exprimer, puisqu'un avis de manifestation
d'intérêt n'attend pas les mêmes champs qu'un avis d'attribution. Un champ
`required` du YAML reste critique en toute circonstance.

Pour qu'un champ ajouté au YAML soit validé sans toucher au code, il suffit de
suivre la convention de nommage : `montant_*`, `date_*`, `delai_*`,
`numero_*`/`*_reference`, `nombre_*`, `*telephone*`.

## Tests

```bash
pip install pytest && python -m pytest -q
```

171 tests, aucun appel réseau ni clé API : les providers LLM, les moteurs PDF et
OCR sont injectés en doublure. Le bulletin de test (`tests/conftest.py`) reproduit
les pièges réels — annonce à cheval sur deux pages, page rescannée, trou de
numérotation, titre non classable, annonce tronquée.

## Corrections apportées à la base existante

- `pipeline.py` importait `layer2_segment` qui vit dans `new_logic/` → le pipeline
  ne démarrait pas (`ImportError`).
- `pipeline.py` et `viewdashboard.py` lisaient `avis_category` / `avis_type`, jamais
  écrits par la segmentation (qui produit `category` / `marche_type`) → catégorie et
  type de marché sortaient toujours vides.
- `layer1_extract.py` écrasait tout le document dans une **unique page virtuelle**,
  détruisant les numéros de page. Remplacé par `core/pages.py`, qui les conserve.
- `new_logic/doublon_detection.py` exécutait un appel sur un chemin Windows en dur
  au niveau module → tout import du fichier échouait.
- `_is_valid()` rechargeait et parsait `schema_config.yaml` à chaque unité validée.
- `extract_unit()` plantait l'appelant si le modèle rendait un JSON valide mais pas
  un objet (liste, chaîne) ; c'est désormais un échec de provider, donc un repli.
- `init_db(db_path=DB_PATH)` capturait le chemin à la définition de la fonction.
- Sur un vrai PDF, aucun titre n'était détecté (la couche de texte n'a pas de
  `#`) : tout le tri par titre s'effondrait. Corrigé par la promotion des titres
  d'après leur mise en forme.
- `OcrReport.to_markdown()` ajoutait son pied de page numéroté alors que la page
  portait déjà le numéro imprimé : une page vide était fabriquée à chaque
  frontière.

## Données locales (non versionnées)

PDF, Markdown sources, `wordlist.txt`, `urls_*.txt`, `*_output.json`, `output/`,
`*.db`, `feedback.db`, rapports QA et dashboards — voir `.gitignore`.

## Sécurité

Aucune clé en dur : `layer3_extract_llm.py` lit uniquement l'environnement. Si des
clés ont déjà été poussées, les révoquer sur OpenRouter / Google AI Studio.
Variables attendues : `OPENROUTER_API_KEY`, `GEMINI_API_KEY`,
`OPENROUTER_XIAOMI_MODEL`, `OPENROUTER_MISTRAL_MODEL`.
