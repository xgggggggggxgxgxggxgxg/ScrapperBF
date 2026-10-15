# Valeurs clés d'une annonce

Relevé **empirique** sur 107 annonces réelles, extraites des bulletins N°4500,
4502, 4503 et 4505 (le supplément 4503(bis) ne contient que des résultats).
Les pourcentages sont la part des annonces où la notion est présente : ils
déterminent la priorité des champs, et donc ce qui déclenche une alerte.

## 1. Lues directement dans la maquette (fiabilité ~100 %)

Ces trois valeurs viennent des bandes colorées, pas du texte : aucun modèle
n'intervient, et elles délimitent l'annonce.

| Valeur | Source | Exemples |
|---|---|---|
| `categorie` | bande **rouge** `(1,0,0)` | Travaux · Fournitures et Services courants · Prestations intellectuelles |
| `emetteur` | bande **jaune** `(1,1,0)` | REGION DU GUIRIKO · COMMUNE DE KOUMBIA · OFFICE NATIONAL DE LA SECURITE ROUTIERE |
| `objet` | bande **cyan** `(0,1,1)` | « Réalisation et réhabilitation d'infrastructures hydrauliques dans la commune de Koloko » |
| `portee` | déduite de `emetteur` | NATIONAL (appel d'offres simple) · REGIONAL (collectivité) · INDETERMINE |
| `page_debut` / `page_fin` | position des bandes | p27 · p22-25 |

## 2. Étiquetées dans l'en-tête du corps (extraction sans LLM possible)

Lignes de la forme `Étiquette : valeur`, juste sous les bandes.

| Valeur | Fréquence | Exemple relevé |
|---|---|---|
| `financement` | **68 %** étiqueté, 78 % présent | `Financement : Budget ONASER, exercice 2026` |
| `montant_previsionnel` | **33 %** étiqueté, 68 % présent | `Montant prévisionnel : vingt un million … (21 186 441) FCFA HTVA, soit … (25 000 000) FCFA TTC` |
| `numero_reference` | 65 % | `n° : 2026-23/MSECU/SG/ONASER/DMP` |
| `autorite_contractante` | 9 % | `Identifiant de l'Autorité contractante : Office National de la Sécurité Routière` |
| `maitre_ouvrage` | 8 % | `Maître d'Ouvrage : Institut Deen Kanu (IDK)` |
| `maitre_ouvrage_delegue` | 6 % | `Maître d'Ouvrage Délégué : Focus Sahel Development` |
| `lots[]` | 34 % | `Lot 1 : acquisition de consommables informatique au profit de la DGI` |

## 3. Dans les clauses numérotées du corps (extraction LLM)

| Valeur | Fréquence | Remarque |
|---|---|---|
| `type_avis` | **86 %** | Avis de demande de prix · Appel d'offres ouvert/accéléré · Avis à manifestation d'intérêt · Demande de propositions |
| `date_limite_depot` | **78 %** | avec heure et lieu ; l'heure est réglementaire, son absence signale une extraction tronquée |
| `date_ouverture_plis` | **77 %** | souvent le même jour, 30 min après le dépôt |
| `garantie_soumission` | **74 %** | montant en FCFA |
| `delai_execution` | **71 %** | « vingt un (21) jours » · « trois (03) mois » — en lettres **et** en chiffres |
| `conditions_eligibilite` | 68 % | agréments (B2/B3/B4 du Ministère de la Construction), situation fiscale |
| `renseignements_contact` | 66 % | adresse, `(00226) 25 37 44 78`, courriel, horaires |
| `delai_validite_offres` | 39 % | « engagés pendant 90 jours » |
| `acquisition_dossier` | 38 % | lieu de retrait + `montant non remboursable de vingt cinq mille (25 000) FCFA` |
| `reservation` | 11 % | « réservée aux petites et moyennes entreprises » · « à Direction féminine » |
| `visite_site` | 3 % | « La visite des lieux est obligatoire » |
| `rectificatif_de` | 2 % | `Rectificatif du Quotidien N°4502 … page 49` |

## 4. Priorité par catégorie

Ce que l'absence d'une valeur veut dire n'est pas le même selon la catégorie.

| Catégorie | Critique | Important | Sans objet |
|---|---|---|---|
| **Travaux** | objet, émetteur, date limite de dépôt, référence | agrément, délai d'exécution, garantie, montant prévisionnel | — |
| **Fournitures et Services courants** | objet, émetteur, date limite de dépôt, référence | montant prévisionnel, financement, lots | visite de site |
| **Prestations intellectuelles** | objet, émetteur, date limite de dépôt | conditions d'éligibilité, contact, domaines | montant prévisionnel (une AMI ne chiffre presque jamais), garantie |

## 5. Particularités du corpus à ne pas perdre

- **Montants écrits deux fois**, en lettres puis en chiffres : « vingt cinq
  millions (25 000 000) FCFA ». C'est une redondance exploitable — les deux
  doivent concorder, sinon l'OCR ou le modèle a fauté.
- **HTVA et TTC donnés ensemble** : deux champs, pas un.
- **Annonces multi-pages** : l'annonce p22-25 du 4503 occupe quatre pages sans
  répéter ses bandes. Seule la bande jaune suivante marque la fin.
- **Rectificatifs** : une annonce peut en corriger une autre publiée dans un
  numéro antérieur ; elle doit porter la référence corrigée, sinon elle est
  inexploitable.
- **Maître d'ouvrage délégué** : l'émetteur de la bande jaune est parfois un
  bureau privé mandaté (FOCUS SAHEL DEVELOPMENT), ni ministère ni collectivité.
- **Suppléments « (bis) »** : documents de résultats seuls, sans aucune bande
  d'annonce. Les traiter comme un bulletin d'avis produirait zéro annonce et un
  faux diagnostic.
