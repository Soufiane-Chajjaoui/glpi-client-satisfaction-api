# GLPI Light Pipeline

Pipeline analytique medallion (Bronze → Silver → Gold) + API REST pour GLPI.

## Structure

```
glpi-light/
  apps/
    bronze/        # Ingestion MySQL → PostgreSQL (schema bronze)
    silver/        # Nettoyage HTML + features → schema silver
    gold/          # Star schema + NLP sentiment + alertes → schema gold/gold_alert
  api/             # API REST FastAPI
  lib/             # Utilitaires (DB, watermarks, Polars helpers, ONNX)
  setup/           # Initialisation des schemas PostgreSQL
  models/onnx/     # Modele DistilCamemBERT (ONNX)
  requirements-prod.txt  # Dependances runtime (sans torch)
  requirements.txt       # Dev : transformers + torch (export modele)
  pyproject.toml         # Metadonnees projet + dependances
  docker-compose.yml  # PostgreSQL 16 + MailHog + API
  Dockerfile.api      # Build image (single-stage, runtime only)
  pipeline.py         # Orchestrateur CLI
```

## Prérequis

- Python 3.11+
- Docker Desktop (pour PostgreSQL + MailHog)
- MySQL 5.7+ (source GLPI)
- **2 Go RAM + 2 cœurs CPU** minimum pour le pipeline NLP

## Installation

### 1. Cloner et créer l'environnement

```bash
git clone <url> glpi-light
cd glpi-light
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate # Linux/Mac
```

### 2. Installer les dépendances

```bash
# Production (runtime only — sans torch)
pip install -r requirements-prod.txt

# Développement (export du modèle ONNX)
pip install -r requirements.txt
```

### 3. Configuration

Créez un fichier `.env` à la racine :

```env
# PostgreSQL (medallion)
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=glpi_light
POSTGRES_USER=glpi
POSTGRES_PASSWORD=glpi

# MySQL source GLPI
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_DB=glpi
MYSQL_USER=root
MYSQL_PASSWORD=

# MailHog (alertes)
SMTP_HOST=localhost
SMTP_PORT=1025
SMTP_USER=
SMTP_PASSWORD=

# API
API_HOST=0.0.0.0
API_PORT=8000

# Mode test : True = sentiment neutre (pas de modèle ONNX)
TEST_MODE=True
```

### 4. Télécharger le modèle ONNX

Le modèle DistilCamemBERT (ONNX) est requis pour l'analyse NLP :

1. Téléchargez le dossier `onnx/` depuis [Google Drive](https://drive.google.com/drive/folders/1hnMD3Lsk6mLwXK6RRvr3pPKsE_zD7pKQ?usp=sharing)
2. Extrayez-le et placez-le dans `models/onnx/`
3. Vérifiez que `models/onnx/model.onnx` existe

Structure attendue :
```
models/onnx/
  config.json
  model.onnx
  model.onnx.data
  sentencepiece.bpe.model
  special_tokens_map.json
  tokenizer_config.json
  tokenizer.json
```

### 5. Lancer les services

```bash
docker compose up -d
```

MailHog UI : http://localhost:8025

## Lancement

### 1. Démarrer les services

```bash
docker compose up -d
```

MailHog UI : http://localhost:8025

### 2. Initialiser les schemas

```bash
python setup/init_db.py
```

### 3. Lancer le pipeline complet

```bash
python pipeline.py --all
```

Ou par etape :

```bash
python pipeline.py --bronze         # MySQL → bronze (incrémental via date_mod)
python pipeline.py --silver         # bronze → silver (incrémental via date_mod, lookback 1h)
python pipeline.py --gold           # silver → gold (incrémental via date_mod, upsert fact table, limit 100 test)
python pipeline.py --gold --gold-limit 100  # Test gold avec 100 tickets seulement
python pipeline.py --critical       # Détection tickets critiques
python pipeline.py --alerts         # Envoi email via MailHog
```

### 4. Lancer l'API REST

```bash
# En local
python -m uvicorn api.main:app --reload

# Ou via Docker
docker compose up -d api
```

API : http://localhost:8000 — Docs : http://localhost:8000/docs

## Architecture du Pipeline

```
MySQL (GLPI)
  │
  ▼
┌────────────┐
│   BRONZE   │  Ingestion brute (MySQL → PostgreSQL schema bronze)
│            │  Full load + incremental via watermarks (date_mod)
└────────────┘
  │
  ▼
┌────────────┐
│   SILVER   │  clean_text (HTML unescape + signatures email)
│            │  + clean_content / clean_comment
│            │  + features : resolution_time, first_response,
│            │    is_critical, nps_score, status_label
│            │  Incrémental : watermark date_mod ou id
│            │  - Tables >100 lignes : upsert au lieu de replace
│            │  - Petites tables (<100) : replace complet
└────────────┘
  │
  ▼
┌────────────┐
│    GOLD    │  Star schema (6 dimensions + fact_ticket_satisfaction)
│            │  Incrémental : watermark gold_tickets sur date_mod
│            │  - Full au 1er run, puis seulement les tickets modifiés
│            │  - Dimensions : replace complet (petites tables)
│            │  - Fact table : upsert (DELETE+INSERT des tickets modifiés)
│            │  - NLP : inférence sur les tickets modifiés uniquement
│            │
│  NLP SENTIMENT :
│  ──────────────
│  Modele : cmarkea/distilcamembert-base-sentiment (ONNX Runtime)
│  Poids : 130 Mo (quantifiable INT8 → ~35 Mo)
│  Téléchargement : [Google Drive](https://drive.google.com/drive/folders/1hnMD3Lsk6mLwXK6RRvr3pPKsE_zD7pKQ?usp=sharing)
│  → extraire dans `models/onnx/`
│  Dependance runtime : onnxruntime + tokenizers (sans torch)
│
│  1. clean_content/textes deja nettoyes en silver
│  2. Inference ONNX par lots (batch_size=4)
│  3. Scores : sentiment_moyen, sentiment_median, nb_messages
│  4. NLP separe sur commentaires d'enquete (clean_comment)
│
│  SCORE COMPOSITE (1-5) :
│  Enquete cliente → avg(etoile_client, NLP commentaire)
│  Pas d'enquete  → 50% comportemental + 50% NLP
│
│  Score comportemental (5 facteurs ponderes) :
│  - pts_tto ............ 15%
│  - pts_ttr ............ 25%
│  - pts_priority ....... 30%
│  - pts_solutions ...... 10%
│  - pts_followups ...... 20%
└────────────┘
  │
  ├──► gold_alert.critical_tickets
  └──► Email alerts via MailHog SMTP
```

## Déploiement Docker

```bash
# Build image (runtime only, ~150 Mo pip + modele 130 Mo)
docker build -f Dockerfile.api -t glpi-light-api .

# Lancer avec 2 Go / 2 coeurs
docker run -d --memory=2g --cpus=2 --name glpi-light-api -p 8000:8000 --env-file .env glpi-light-api

# Ou avec docker-compose (ressources preconfigurees)
docker compose up -d
```

Le `.dockerignore` exclut `.venv/`, `__pycache__/`, `docs/`, etc.

## Optimisation memoire

Le pipeline NLP tient dans **2 Go RAM / 2 cœurs** avec 12 000+ tickets :
- Modele ONNX : ~130 Mo + runtime
- Textes charges par lots de 4
- `gc.collect()` entre chaque batch
- `enable_cpu_mem_arena=False` dans ONNX Runtime

Si OOM persiste, quantifier le modele en INT8 :
```python
import onnx
from onnxruntime.quantization import quantize_dynamic, QuantType
quantize_dynamic("model.onnx", "model_int8.onnx", weight_type=QuantType.QUInt8)
```

## Variables d'environnement (.env)

| Variable | Defaut | Description |
|----------|--------|-------------|
| `POSTGRES_*` | localhost:5432 | PostgreSQL (medallion) |
| `MYSQL_*` | localhost:3306 | MySQL source GLPI |
| `SMTP_*` | localhost:1025 | MailHog alertes email |
| `API_HOST/PORT` | 0.0.0.0:8000 | API REST |
| `TEST_MODE` | True | True = sentiment neutre, False = modele ONNX |

## Planification

### APScheduler (recommandé)

```bash
python scheduler.py
```

Pipeline complet chaque jour a 6h00.

### cron / Task Scheduler

- Windows : `cron/pipeline_daily.ps1`
- Linux : `cron/pipeline_daily.sh`
