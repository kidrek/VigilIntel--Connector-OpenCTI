# OpenCTI Connector — VigilIntel STIX Importer

Connecteur **external-import** pour [OpenCTI](https://www.opencti.io/) qui ingère automatiquement les rapports de threat intelligence publiés quotidiennement au format **STIX 2.x** depuis le dépôt GitHub [VigilIntel](https://github.com/kidrek/VigilIntel).

---

## Fonctionnalités

- **Récupération automatique quotidienne** des rapports STIX
- **Backfill** : lors de la première exécution, récupère les X derniers jours
- **Déduplication** via le state OpenCTI (pas de ré-importation)
- **Gestion des erreurs** : rapports manquants (404), erreurs réseau, JSON invalide — sans interruption
- **Bilingue** : rapports disponibles en `fr` (français) et `en` (anglais)
- **Configurable** via variables d'environnement ou `config.yml`

## Architecture

```
VigilIntel--Connector-OpenCTI/
├── docker-compose.yml
├── Dockerfile
├── entrypoint.sh
├── requirements.txt
├── README.md
└── src/
    ├── config.yml.sample
    ├── main.py                  # Point d'entrée
    └── lib/
        ├── __init__.py
        └── vigilintel.py        # Logique du connecteur
```

## Flux de fonctionnement

```
┌────────────────────┐
│  Initialisation    │
│  (config + state)  │
└────────┬───────────┘
         ▼
┌────────────────────┐
│  Calcul des dates  │──── Première fois ? → backfill X jours
│  à traiter         │──── Déjà exécuté ? → depuis last_date + 1
└────────┬───────────┘
         ▼
┌────────────────────┐
│  Pour chaque date: │
│  1. Build URL      │
│  2. Download JSON  │
│  3. Validate STIX  │
│  4. Send to OpenCTI│
└────────┬───────────┘
         ▼
┌────────────────────┐
│  Mise à jour state │
│  + Sleep N heures  │
└────────────────────┘
```

## Configuration

### Variables d'environnement

| Variable                    | Description                          | Défaut    |
| --------------------------- | ------------------------------------ | --------- |
| `OPENCTI_URL`               | URL de la plateforme OpenCTI         | —         |
| `OPENCTI_TOKEN`             | Token API OpenCTI                    | —         |
| `CONNECTOR_ID`              | UUID v4 unique du connecteur         | —         |
| `CONNECTOR_TYPE`            | Type de connecteur                   | `EXTERNAL_IMPORT` |
| `CONNECTOR_NAME`            | Nom affiché dans OpenCTI             | `VigilIntel` |
| `CONNECTOR_SCOPE`           | Scope du connecteur                  | `stix2`   |
| `CONNECTOR_LOG_LEVEL`       | Niveau de log                        | `info`    |
| `VIGILINTEL_LANGUAGE`       | Langue des rapports (`fr` / `en`)    | `fr`      |
| `VIGILINTEL_LOOKBACK_DAYS`  | Nombre de jours de backfill          | `7`       |
| `VIGILINTEL_INTERVAL_HOURS` | Intervalle entre exécutions (heures) | `24`      |

## Déploiement

### Docker (recommandé)

1. Cloner le dépôt :
   ```bash
   git clone https://github.com:kidrek/VigilIntel--Connector-OpenCTI.git
   cd VigilIntel--Connector-OpenCTI
   ```

2. Construire l'image :
   ```bash
   docker compose build --no-cache
   ```

3. Configurer les variables dans `docker-compose.yml` :
   - `OPENCTI_URL` et `OPENCTI_TOKEN`
   - `CONNECTOR_ID` (générer un UUID : `python -c "import uuid; print(uuid.uuid4())"`)

4. Lancer :
   ```bash
   docker compose up -d
   ```

### Manuel (développement)

```bash
cd VigilIntel--Connector-OpenCTI
python -m venv env
source env/bin/activate
pip install -r requirements.txt

cp src/config.yml.sample src/config.yml
# Éditer src/config.yml avec vos paramètres

python src/main.py
```

## Gestion de l'état

Le connecteur persiste la date du dernier rapport traité dans le **state OpenCTI** :

```json
{
  "last_processed_date": "2026-02-07T00:00:00+00:00",
  "last_run": "2026-02-07T02:15:30.123456+00:00"
}
```

- **Premier lancement** : backfill des `VIGILINTEL_LOOKBACK_DAYS` derniers jours
- **Lancements suivants** : seules les dates manquantes sont traitées
- **Reset** : supprimer le state du connecteur dans OpenCTI pour relancer un backfill complet

## Source des données

Les rapports sont téléchargés depuis le dépôt GitHub de VigilIntel via les URLs `raw.githubusercontent.com` :

```
https://raw.githubusercontent.com/kidrek/VigilIntel/main/{YEAR}/{MONTH}/{YEAR}-{MONTH}-{DAY}-report.stix_{LANG}.json
```

Exemple :
```
https://raw.githubusercontent.com/kidrek/VigilIntel/main/2026/02/2026-02-07-report.stix_fr.json
```

## Logs

Le connecteur utilise les niveaux de log standard OpenCTI :

- **INFO** : démarrage, dates traitées, bundles importés
- **WARNING** : rapports manquants (404), configuration invalide
- **ERROR** : erreurs réseau, JSON invalide, échecs d'envoi

## Évolutions possibles

- Support multi-langue simultané (fr + en)
- Mode dry-run
- Retry configurable avec backoff exponentiel
- Vérification de disponibilité avant téléchargement
- Support d'autres sources VigilIntel

## Licence

Apache 2.0
