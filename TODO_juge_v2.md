# Reste à faire — Flux « Juge v2 » (AVIS)

État au dernier push sur `claude/trusting-mccarthy-tztl4g`. 258 tests hors-ligne passent.

## ✅ Fait

- Extraction A/B (`extraction.py`), scoreur code pur (`scoreur.py`), juge-LLM 3ᵉ modèle
  distinct (`juge_llm.py`), orchestrateur vert/jaune/rouge (`juge.py`).
- Règles par situation (`regles.py` + `regles.json`), revue humaine (`revue_humaine.py`)
  + CLI (`revue_cli.py`).
- Champs `critique: true` dans `schema_config.yaml`.
- Branchement pipeline AVIS derrière `JUGE_V2` (défaut actif). RÉSULTATS inchangé.
- 7 améliorations post-revue (modèle juge distinct, scoreur indépendant, cache, logs,
  confiance par couleur, montant en lettres) + 4 correctifs de la revue adverse round 1.
- **Revue adverse round 2** (terminée, avec vérification) : 3 correctifs supplémentaires —
  `load_rules` robuste aux `regles.json` mal formés, dossier corrigé non écrasé au
  re-traitement, écritures atomiques (regles + dossiers).
- README à jour (flux v2 + CLI).

## 🔜 À faire

### Priorité haute
1. **Run réel end-to-end** avec de vraies clés API sur un PDF de Quotidien. Tout est
   testé **hors-ligne** (providers injectés) ; le flux n'a **jamais** tourné avec de
   vrais modèles. À vérifier : A et B distincts, juge-LLM sur un 3ᵉ modèle, création
   effective des dossiers 🟡/🔴, coût/tokens.

### Priorité moyenne
3. **Calibrer les seuils** sur de vrais avis. Valeurs posées **par défaut, non
   calibrées** : scoreur `bon≥85 / mauvais<60` (sévère `92/75`), plafonds de confiance
   `vert 1.0 / jaune 0.75 / rouge 0.5`, divergence. (L'ancien juge était calibré sur
   86 avis ; refaire l'équivalent pour v2.)
4. **Tests d'intégration bout-en-bout** de `pipeline.process_pdf` avec providers
   injectés (actuellement : unités + jointure `to_judge_compat`, pas le `process_pdf`
   complet, qui exige un PDF).
5. **Décider : `regles.json` versionné ou local** (actuellement versionné `[]` ;
   alternative : le gitignorer comme `feedback_store.json`).

### Priorité basse / décisions ouvertes
6. **Appliquer (ou non) la logique v2 à la section RÉSULTATS** (reportée ; aujourd'hui
   `judge.judge_resultats` inchangé).
7. **Sort de l'ancien flux** : si v2 est adopté définitivement, nettoyer
   `consensus_extract.py` / `divergence_analyzer` (encore utilisés en fallback
   `JUGE_V2=0`) — ou garder le repli.
8. **`span_scorer`** (scoreur comportemental optionnel) : laissé de côté pour v2 ;
   décider s'il se branche en parallèle du nouveau juge.
9. **CLI** : pas de pagination ni de recherche dans les dossiers ; possible ajout si le
   volume de revue grandit.
