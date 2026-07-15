# Data Catalog — Couche Gold (GLPI Sentiments Analysis)

## Objectif global de la couche Gold

**Propriétaires :** Soufiane CHAJJAOUI · Hicham KAOU · Manal LKHMAMARA

La couche **Gold** est la dernière étape de l'architecture médaillon (Bronze → Silver → **Gold**). Elle transforme les données nettoyées et enrichies de Silver en un **schéma en étoile** (star schema) prêt pour l'analytics, le reporting et l'API REST.

Elle produit :
- **6 dimensions** de référence (entités, catégories, SLAs, utilisateurs, statuts, dates)
- **1 table de faits** centrale : `fact_ticket_satisfaction` (analyse de satisfaction par ticket)
- **1 table d'alertes** : `critical_tickets` (tickets proches de la rupture SLA)
- Un **score composite de satisfaction** (1-5) mêlant comportement, NLP et enquête client
- Une **analyse NLP** via DistilCamemBERT (ONNX) sur les contenus des tickets

---

## Dimensions

### `dim_entity` — Entités (clients/organisations)

| Colonne | Type | Description |
|---|---|---|
| `entity_id` | PK | Identifiant GLPI de l'entité |
| `entity_name` | Text | Nom court |
| `entity_completename` | Text | Chemin hiérarchique complet |
| `entity_level` | Int | Profondeur dans l'arbre |
| `parent_entity_id` | FK | Entité parente |
| `town` | Text | Ville |
| `country` | Text | Pays |
| `entity_comment` | Text | Commentaire / description |

Rôle : Permet de filtrer/regrouper les tickets par entité cliente.

---

### `dim_category` — Catégories ITIL

| Colonne | Type | Description |
|---|---|---|
| `category_id` | PK | Identifiant GLPI |
| `category_name` | Text | Nom court |
| `category_completename` | Text | Chemin hiérarchique complet |
| `category_level` | Int | Profondeur dans l'arbre |
| `parent_category_id` | FK | Catégorie parente |
| `entity_id` | FK | Entité propriétaire |
| `is_incident` | Bool | Applicable aux incidents |
| `is_request` | Bool | Applicable aux demandes |

Rôle : Catégorise la nature du ticket (incident, demande, etc.).

---

### `dim_sla` — Engagements de service (SLA)

| Colonne | Type | Description |
|---|---|---|
| `sla_id` | PK | Identifiant GLPI |
| `sla_name` | Text | Nom du SLA |
| `sla_type` | Int | 0 = TTR, 1 = TTO |
| `sla_type_label` | Text | "TTR", "TTO" ou "Inconnu" |
| `number_time` | Int | Valeur seuil |
| `time_unit` | Text | "minute", "hour" ou "day" |
| `sla_seconds` | Float | Seuil converti en secondes |
| `end_of_working_day` | Bool | Pause hors heures ouvrées |
| `use_ticket_calendar` | Bool | Calendrier spécifique |
| `entity_id` | FK | Entité propriétaire |

Rôle : Référentiel des engagements contractuels de résolution (TTR) et de réponse (TTO).

---

### `dim_user` — Utilisateurs

| Colonne | Type | Description |
|---|---|---|
| `user_id` | PK | Identifiant GLPI |
| `username` | Text | Login |
| `firstname` | Text | Prénom |
| `realname` | Text | Nom |
| `full_name` | Text | Prénom + Nom |
| `entity_id` | FK | Entité |
| `locations_id` | FK | Localisation |
| `is_active` | Bool | Compte actif |
| `language` | Text | Langue préférée |

Rôle : Référence des utilisateurs (techniciens, demandeurs).

---

### `dim_status` — Statuts des tickets

| `status_id` | `status_label` |
|---|---|
| 1 | Nouveau |
| 2 | En cours |
| 3 | Planifié |
| 4 | En attente |
| 5 | Résolu |
| 6 | Clos |

6 valeurs statiques. Rôle : Donne le libellé lisible du statut d'un ticket.

---

### `dim_date` — Calendrier

| Colonne | Type | Description |
|---|---|---|
| `date_id` | PK | YYYYMMDD (Int32) |
| `date_val` | Date | Date réelle |
| `year` | Int | Année |
| `month` | Int | Mois (1-12) |
| `quarter` | Int | Trimestre (1-4) |
| `week_of_year` | Int | Semaine ISO |
| `day_of_month` | Int | Jour du mois |

Rôle : Axe temporel pour les analyses de tendances (daily, monthly).

---

## Table de faits

### `fact_ticket_satisfaction` — Analyse de satisfaction par ticket

Cœur de l'analytics. Une ligne = un ticket GLPI.

| Colonne | Type | Description |
|---|---|---|
| `ticket_id` | PK | Identifiant GLPI du ticket |

**Clés étrangères vers les dimensions :**

| Colonne | Cible | Description |
|---|---|---|
| `entity_id` | `dim_entity` | Entité cliente |
| `category_id` | `dim_category` | Catégorie ITIL |
| `status_id` | `dim_status` | Statut actuel |

**Attributs de base :**

| Colonne | Type | Description |
|---|---|---|
| `ticket_date` | Text | Date de création |
| `priority` | Int | Priorité (1=critique, 5=très basse) |
| `priority_weight` | Int | Poids inversé (1→10, 5→1) |

**Conformité SLA :**

| Colonne | Type | Description |
|---|---|---|
| `tto_ok` | Bool | Acknowledgement dans les temps |
| `ttr_ok` | Bool | Résolution dans les temps |

**Activité :**

| Colonne | Type | Description |
|---|---|---|
| `nb_solutions` | Int | Nombre de solutions |
| `nb_followups` | Int | Nombre de suivis |

**Scoring comportemental (1-5) :**

| Colonne | Poids | Description |
|---|---|---|
| `pts_tto` | 15 % | 5 si TTO respecté, 1 sinon |
| `pts_ttr` | 25 % | 5 si TTR respecté, 1 sinon |
| `pts_priority` | 30 % | Priorité normalisée (1-5) |
| `pts_solutions` | 10 % | Pénalité inversée (5 pts si 0 solution → 1 pt si ≥5) |
| `pts_followups` | 20 % | Pénalité inversée (5 pts si 0 suivi → 1 pt si ≥10) |
| `score_comportemental` | — | Somme pondérée des 5 sous-scores |

**NLP Sentiment (DistilCamemBERT, ONNX, 1-5 stars) :**

| Colonne | Description |
|---|---|
| `sentiment_moyen` | Moyenne NLP sur tous les messages du ticket |
| `sentiment_median` | Médiane NLP |
| `nb_messages` | Nombre de messages analysés |
| `score_sentiment` | `sentiment_moyen` avec fallback 3.0 |
| `sentiment_comment` | Moyenne NLP sur les commentaires d'enquête uniquement |
| `nb_comment_messages` | Nombre de commentaires d'enquête analysés |

**Enquête client :**

| Colonne | Description |
|---|---|
| `satisfaction_score` | Note brute de l'enquête |
| `etoile_client` | Note client ramenée sur 5 |
| `nps_score` | Score NPS (0-10) |
| `nps_category` | `promoter` (≥4), `passive` (≥3), `detractor` (< 3), `unknown` |

**Score composite (1-5) — métrique principale :**

| Condition | Calcul |
|---|---|
| Enquête + commentaire NLP | `avg(etoile_client, sentiment_comment)` |
| Enquête seule | `etoile_client` |
| Aucune enquête | `50% × score_comportemental + 50% × score_sentiment` |

**Métadonnées :**

| Colonne | Valeur |
|---|---|
| `modele_nlp` | `"distilcamembert"` |
| `gold_ingestion_date` | Timestamp d'ingestion |
| `gold_processing_status` | `"BUSINESS_READY"` |
| `data_version` | 1 |

---

## Table d'alertes

### `gold_alert.critical_tickets` — Tickets critiques (risque de rupture SLA)

| Colonne | Description |
|---|---|
| `ticket_id` | ID du ticket |
| `entities_id` / `client_nom` | Entité cliente |
| `titre` | Titre du ticket |
| `date_creation`, `status`, `priority` | Infos de base |
| `sla_name`, `ttr_heures` | SLA TTR applicable et son seuil |
| `nb_followups`, `nb_tasks`, `nb_solutions` | Activité |
| `nb_actions_total` | Somme des actions |
| `age_heures` | Âge du ticket |
| `heures_restantes_ttr` | Temps restant avant rupture |
| `est_critique` | Toujours 1 |
| `date_alerte` | Timestamp de détection |

Détection : tickets ouverts (statut "En cours") dont l'âge ≥ seuil TTR - 24h et activité nulle.

---

## Schéma résumé

```
bronze (ingestion brute MySQL) ─→ silver (nettoyage + features) ─→ gold (star schema)
                                                                       │
                                                                       ├─ dim_entity
                                                                       ├─ dim_category
                                                                       ├─ dim_sla
                                                                       ├─ dim_user
                                                                       ├─ dim_status
                                                                       ├─ dim_date
                                                                       ├─ fact_ticket_satisfaction
                                                                       └─ gold_alert.critical_tickets
```
