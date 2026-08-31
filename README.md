# flight-watcher

Surveillance de prix de vols avec alertes Telegram instantanées.

Tu définis un trajet et un seuil. Toutes les 30 minutes, un job GitHub Actions
interroge Google Flights (et Ryanair pour les départs de Charleroi), compare au
seuil, et t'envoie une notification Telegram dès que c'est bon. L'historique des
prix est conservé et affiché sur un dashboard web.

**Pourquoi pas les alertes Google Flights :** elles sont groupées et envoyées par
e-mail, souvent plusieurs heures après la baisse. Ici, le délai max entre une
baisse et ta notification est de 30 minutes, et ça arrive en push sur ton
téléphone.

---

## Installation (environ 15 minutes)

### 1. Créer le bot Telegram

1. Dans Telegram, ouvre une conversation avec **@BotFather**.
2. Envoie `/newbot`, choisis un nom puis un identifiant (qui doit finir par `bot`).
3. BotFather te renvoie un **token** du type `8123456789:AAH...`. Garde-le.
4. Ouvre une conversation avec **ton** nouveau bot et envoie-lui `/start`
   (indispensable : sans ça, le bot n'a pas le droit de t'écrire).
5. Récupère ton **chat id** : ouvre dans un navigateur
   `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
   et lis la valeur `"chat":{"id":123456789`.

### 2. Créer le dépôt GitHub

```bash
cd flight-watcher
git init && git add . && git commit -m "flight-watcher"
gh repo create flight-watcher --public --source=. --push
```

> **Public ou privé ?** Sur un dépôt **public**, GitHub Actions est gratuit et
> illimité. Sur un dépôt **privé**, tu as 2 000 minutes/mois — un relevé toutes
> les 30 min en consomme ~2 900. Si tu tiens au privé, passe le cron à
> `0 */2 * * *` (toutes les 2 h). Le dépôt ne contient que des trajets et des
> prix, rien de personnel — le token Telegram vit dans les secrets, jamais dans
> le code.

### 3. Ajouter les secrets

Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**

| Nom | Valeur |
|---|---|
| `TELEGRAM_BOT_TOKEN` | le token de BotFather |
| `TELEGRAM_CHAT_ID` | ton chat id |

### 4. Vérifier que tout répond

Onglet **Actions → Test de configuration → Run workflow**.
Le job teste Google Flights, Ryanair et t'envoie un message Telegram de contrôle.
S'il passe, tout est en place.

### 5. Activer le dashboard (optionnel)

**Settings → Pages → Source : GitHub Actions.**
Le dashboard sera sur `https://<ton-compte>.github.io/flight-watcher/`.

---

## Utiliser au quotidien

### Depuis Telegram

```
/add BRU,CRL BKK 2027-02-06 2027-02-20 600 flex=3 escales=1 #Bangkok février
/list
/seuil bangkok-fevrier 550
/stats bangkok-fevrier
/pause bangkok-fevrier
/suppr bangkok-fevrier
/check
/aide
```

Les commandes sont lues au début de chaque relevé : compte jusqu'à 30 minutes
avant la réponse. Elles modifient `watches.yaml` et sont committées dans le dépôt.

### Depuis le fichier `watches.yaml`

```yaml
watches:
  - id: bangkok-fevrier
    label: Bangkok en février
    origin: [BRU, CRL, AMS]      # plusieurs aéroports de départ : le moins cher gagne
    destination: [BKK]
    depart: 2027-02-06
    return: 2027-02-20           # retire cette ligne pour un aller simple
    threshold: 600               # alerte dès que le total passe sous 600 €
    flex_days: 3                 # teste aussi ± 3 jours
    max_stops: 1
    providers: [google_flights]  # ajoute ryanair pour les départs de Charleroi
    passengers: {adults: 2, children: 1}
    alert_on_drop: true          # alerte aussi sur une chute anormale
    enabled: true
```

### Réglages globaux

| Clé | Défaut | Rôle |
|---|---|---|
| `cooldown_hours` | 12 | délai avant de répéter la même alerte |
| `renotify_drop_pct` | 5 | ...sauf si le prix rebaisse encore de X % |
| `drop_pct` | 15 | déclenche une alerte « chute » à X % sous la médiane 30 j |
| `drop_min_samples` | 8 | nombre de relevés avant d'activer l'alerte chute |
| `flex_mode` | `shift` | `shift` décale aller+retour ensemble ; `matrix` teste toutes les combinaisons |
| `max_queries_per_run` | 120 | garde-fou sur la durée du job |
| `quiet_hours` | — | ex. `[23, 7]` pour ne rien envoyer la nuit |

---

## Quand tu reçois une alerte

Le message contient le prix, la compagnie, les escales, les repères sur 30 jours
et un lien vers la recherche. **Les prix bougent vite** : ouvre le lien et
vérifie avant de réserver. Le lien ouvre la recherche, pas une réservation
pré-remplie — Google Flights ne permet pas de figer une offre depuis l'extérieur.

## En local

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...

python -m watcher.cli selftest              # vérifie la config
python -m watcher.cli run --dry-run         # relève sans rien envoyer
python -m watcher.cli run --watch bangkok-fevrier
python -m watcher.cli list
python -m watcher.cli stats bangkok-fevrier
```

## Comment ça marche

```
GitHub Actions (cron 30 min)
   ├─ lit les commandes Telegram en attente  → met à jour watches.yaml
   ├─ pour chaque surveillance active
   │     origines × destinations × dates flexibles
   │     → Google Flights (protobuf interne, sans clé API)
   │     → Ryanair (API publique de disponibilité)
   │     → garde le meilleur prix
   ├─ compare au seuil + à la médiane 30 jours → alerte Telegram
   ├─ ajoute une ligne à data/history.jsonl
   └─ commit dans le dépôt → GitHub Pages redéploie le dashboard
```

Pas de base de données, pas de serveur, pas de clé API : l'historique est un
fichier JSONL versionné dans le dépôt.

## Limites connues

- **Google Flights n'a pas d'API publique.** Le fournisseur `google_flights`
  utilise l'endpoint interne du site. C'est fiable au quotidien mais ça peut
  casser si Google change son protocole — le job continue alors avec les autres
  fournisseurs et l'incident apparaît dans le résumé du run et sur le dashboard.
- **Amadeus a fermé son portail Self-Service le 17 juillet 2026** et Kiwi/Tequila
  est passé en accès sur invitation : ce n'est plus une option pour un projet
  personnel.
- Les prix affichés sont ceux de la recherche, hors bagages et options.
- GitHub peut décaler un cron de quelques minutes en période de charge.
- Une surveillance très flexible (beaucoup d'origines × `flex_days` élevé) fait
  beaucoup de requêtes : `max_queries_per_run` la tronque plutôt que de faire
  échouer le job.
