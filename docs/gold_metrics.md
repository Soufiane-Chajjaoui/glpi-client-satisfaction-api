# Couche Gold — Métriques & Calculs

## Architecture

```
Silver ──► Gold (star schema)
              ├── dim_entity
              ├── dim_sla
              ├── dim_user
              ├── dim_status
              ├── dim_category
              ├── dim_date
              └── fact_ticket_satisfaction (table de faits)
```

## NLP — Analyse de Sentiment

**Modèle :** `cmarkea/distilcamembert-base-sentiment` (français)

**Textes analysés (regroupés par ticket) :**
1. Contenu du ticket (`content`)
2. Solutions (`glpi_itilsolutions.content`)
3. Followups (`glpi_itilfollowups.content`)

**Labels → Score (STAR_MAPPING) :**

| Label | Score |
|-------|-------|
| 1 star | 1.0 |
| 2 stars | 2.0 |
| 3 stars | 3.0 |
| 4 stars | 4.0 |
| 5 stars | 5.0 |

**Agrégation par ticket :**
- `sentiment_moyen` = moyenne des scores de tous les messages du ticket
- `sentiment_median` = médiane
- `nb_messages` = nombre de textes analysés
- `score_sentiment` = `sentiment_moyen` avec fallback à 3.0 si null

**Cas particulier — commentaires d'enquête :**
Un second pipeline NLP tourne uniquement sur le `comment` des `glpi_ticketsatisfactions` :

| Colonne | Description |
|---------|-------------|
| `sentiment_comment` | Score NLP du commentaire d'enquête |
| `nb_comment_messages` | Nombre de commentaires analysés (0 ou 1) |

## Score Comportemental

5 facteurs pondérés :

| Facteur | Pondération | Calcul | Détail |
|---------|-------------|--------|--------|
| `pts_tto` | 15% | 5 si `takeintoaccount_delay_stat ≤ seuil_TTO`, sinon 1 | Seuil SLA TTO ou fallback ITIL |
| `pts_ttr` | 25% | 5 si `solve_delay_stat ≤ seuil_TTR`, sinon 1 | Seuil SLA TTR ou fallback ITIL |
| `pts_priority` | 30% | Normalisation linéaire 1→5 depuis `PRIORITY_WEIGHT` | Poids × priorité (critique=10 → 5pts, très basse=1 → 1pt) |
| `pts_solutions` | 10% | 5 - (nb_solutions / 5 × 4), cap à 5 | 0 solutions = 5pts, 5+ solutions = 1pt |
| `pts_followups` | 20% | 5 - (nb_followups / 10 × 4), cap à 10 | 0 followups = 5pts, 10+ followups = 1pt |

**Formule :**
```
score_comportemental = pts_tto × 0.15 + pts_ttr × 0.25 + pts_priority × 0.30 + pts_solutions × 0.10 + pts_followups × 0.20
```

**Seuils ITIL (fallback si pas de SLA configuré) :**

| Priorité | TTO (sec) | TTR (sec) |
|----------|-----------|-----------|
| 1 (très haute) | 1 800 (30 min) | 14 400 (4h) |
| 2 (haute) | 14 400 (4h) | 86 400 (24h) |
| 3 (moyenne) | 86 400 (24h) | 259 200 (72h) |
| 4 (basse) | 259 200 (72h) | 432 000 (120h) |
| 5 (très basse) | 432 000 (120h) | 1 296 000 (360h) |

**Poids de priorité :**

| Priorité GLPI | Poids |
|---------------|-------|
| 1 (très haute) | 10 |
| 2 (haute) | 6 |
| 3 (moyenne) | 3 |
| 4 (basse) | 1 |
| 5 (très basse) | 1 |

## Score Composite

**Logique de calcul (colonne `score_composite`) :**

```
SI enquête client présente (etoile_client non NULL) ET commentaire NLP disponible :
    score_composite = moyenne(etoile_client, sentiment_comment)

SINON SI enquête présente (etoile_client non NULL) :
    score_composite = etoile_client

SINON (pas d'enquête) :
    score_composite = score_comportemental × 0.5 + score_sentiment × 0.5
```

### Différence clé :
- **Ticket avec enquête** : On utilise UNIQUEMENT les données client (étoiles + NLP du commentaire), le comportemental est ignoré. L'avis client est souverain.
- **Ticket sans enquête** : 50% comportemental (SLA, priorité, followups) + 50% NLP du contenu du ticket.

## Colonnes de `fact_ticket_satisfaction`

| Colonne | Type | Source | Description |
|---------|------|--------|-------------|
| `ticket_id` | int | glpi_tickets.id | Identifiant unique du ticket |
| `entity_id` | int | glpi_tickets.entities_id | Entité propriétaire |
| `category_id` | int | glpi_tickets.itilcategories_id | Catégorie ITIL |
| `status_id` | int | glpi_tickets.status | Statut (1-6) |
| `ticket_date` | text | glpi_tickets.date_creation | Date de création |
| `priority` | int | glpi_tickets.priority | Priorité (1-5) |
| `priority_weight` | int | Calculé | Poids 1-10 selon priorité |
| `tto_ok` | bool | Calculé | SLA TTO respecté |
| `ttr_ok` | bool | Calculé | SLA TTR respecté |
| `nb_solutions` | int | Agrégé | Nombre de solutions |
| `nb_followups` | int | Agrégé | Nombre de followups |
| `pts_tto` | float | Calculé | 5 ou 1 selon respect TTO |
| `pts_ttr` | float | Calculé | 5 ou 1 selon respect TTR |
| `pts_priority` | float | Calculé | Normalisé 1-5 |
| `pts_solutions` | float | Calculé | Pénalité inversée 1-5 |
| `pts_followups` | float | Calculé | Pénalité inversée 1-5 |
| `score_comportemental` | float | Calculé | Pondération des 5 facteurs |
| `score_sentiment` | float | Calculé | NLP avec fallback 3.0 |
| `score_composite` | float | Calculé | Score final (enquête ou 50/50) |
| `etoile_client` | float | glpi_ticketsatisfactions.satisfaction | Note client sur 5 |
| `satisfaction_score` | float | glpi_ticketsatisfactions.satisfaction | Note brute satisfaction |
| `nps_score` | int | Calculé | Score NPS (-100 à 100) |
| `nps_category` | text | Calculé | promoter / passive / detractor |
| `sentiment_moyen` | float | NLP | Moyenne NLP brute (peut être null) |
| `sentiment_median` | float | NLP | Médiane NLP |
| `nb_messages` | int | NLP | Nombre de textes analysés |
| `sentiment_comment` | float | NLP | NLP du commentaire d'enquête |
| `nb_comment_messages` | int | NLP | 0 ou 1 |
| `modele_nlp` | text | Fixe | "distilcamembert" |
| `gold_ingestion_date` | text | Date d'exécution | Timestamp de la pipeline |
| `gold_processing_status` | text | Fixe | "BUSINESS_READY" |
| `data_version` | int | Fixe | 1 |

## Dimensions

| Table | Colonnes clés |
|-------|---------------|
| `dim_entity` | entity_id, entity_name, entity_completename, parent_entity_id, town, country |
| `dim_sla` | sla_id, sla_name, sla_type (0=TTR, 1=TTO), sla_seconds |
| `dim_user` | user_id, username, firstname, realname, is_active |
| `dim_status` | status_id (1-6), status_label |
| `dim_category` | category_id, category_name, category_completename, parent_category_id |
| `dim_date` | date_id (YYYYMMDD), year, month, quarter, week_of_year, day_of_month |

## Nettoyage des Textes (clean_text)

Avant analyse NLP, les textes sont nettoyés :
1. Décodage HTML (`&amp;` → `&`, etc.)
2. Normalisation des retours à la ligne
3. Remplacement `<br>` → `\n`
4. Suppression des balises HTML
5. Suppression des signatures email (`-- `, `Envoyé de mon...`)
6. Suppression des en-têtes de réponse (`De:`, `From:`, `Sent:`, `À:`, `To:`, `Cc:`, `Objet:`, `Subject:`, `Date:`)
7. Suppression des lignes citées (`>`)
8. Normalisation des espaces
