# flight-watcher — module « Promos »

Spec d'ajout. À lire après `PLAN-INTERFACE.md`. Indépendant de celui-ci :
les deux chantiers peuvent être menés dans n'importe quel ordre.

---

## Le principe

Le moteur actuel surveille **un prix sur un trajet précis**. Il ne voit pas
passer les ventes flash, les Promo Rewards Flying Blue ni les erreurs de
tarif — parce que ce ne sont pas des baisses sur *sa* requête, ce sont des
événements annoncés ailleurs.

L'idée : ajouter une **veille éditoriale** à côté de la veille tarifaire.

> L'information sur les promos est publique, même quand la réservation exige
> un compte. Les Promo Rewards Flying Blue sont listés en clair par les sites
> spécialisés dès leur sortie. Le robot n'a donc besoin d'aucun identifiant :
> il lit, recoupe, alerte, et renvoie l'utilisateur vers le site de la
> compagnie où **son propre navigateur** est déjà connecté.

Trois briques indépendantes, par ordre de valeur :

1. **Veille des flux** — lire des flux RSS de bons plans, en extraire les
   destinations, recouper avec les surveillances existantes.
2. **Calendrier des promos** — les fenêtres récurrentes connues, avec un
   bouton d'accès direct à la page offres de chaque compagnie.
3. **Recoupement** — c'est la brique 1 qui produit l'alerte ; listée à part
   parce que c'est elle qui fait toute la valeur.

---

## Brique 1 — Veille des flux

### Sources

⚠️ **Les URL de flux ci-dessous sont à vérifier à l'implémentation** : elles
n'ont pas pu être testées depuis l'environnement où cette spec a été écrite.
Vérifier chacune avec un simple `curl` et écarter celles qui ne répondent pas.

| Source | Flux présumé | Couverture |
|---|---|---|
| The Flight Deal | `theflightdeal.com/feed` | **confirmé** — origines surtout américaines |
| Secret Flying | `secretflying.com/feed/` | mondial, filtres par région |
| Fly4Free | `fly4free.com/feed/` | **européen — le plus pertinent ici** |
| HolidayPirates / TravelPirates | `holidaypirates.com/feed` | européen, offres packagées |
| AwardWallet | à vérifier | Promo Rewards Flying Blue mensuels |
| Milesopedia | à vérifier | francophone, Flying Blue |

Prévoir la liste des flux **dans la configuration**, pas en dur : ces sites
changent d'URL, et il faut pouvoir en ajouter ou en retirer sans redéployer.

### Traitement

```
pour chaque flux
  └─ lire les entrées publiées depuis le dernier passage
     └─ extraire les lieux : codes IATA (\b[A-Z]{3}\b) + noms de villes
        └─ recouper avec les origines et destinations surveillées
           └─ correspondance → alerte Telegram
```

Points d'attention :

- **Filtrer par origine, sinon c'est un déluge.** The Flight Deal publie
  surtout au départ des États-Unis. Ne garder une entrée que si elle mentionne
  une des origines surveillées (BRU, CRL, AMS, CDG…) **ou** si elle concerne
  une destination surveillée sans origine identifiable.
- **Les codes IATA à trois lettres produisent des faux positifs** : « THE »,
  « AND », « NEW », « ONE » sont des codes valides. Filtrer sur un
  dictionnaire des aéroports réellement concernés plutôt que sur la regex
  seule ; ne considérer un code que s'il apparaît dans les surveillances.
- **Noms de villes en plusieurs langues** : « Bangkok » mais aussi
  « Bangkok, Thailand », « BKK ». Prévoir une table de synonymes par
  destination surveillée, alimentable à la main.
- **Déduplication** : les mêmes offres circulent sur plusieurs sites. Retenir
  l'URL de l'entrée et ne pas réalerter sur une entrée déjà vue.
- **Ne jamais faire échouer le relevé de prix** si un flux est mort. Le module
  promos est du bonus : toute erreur est journalisée, jamais bloquante.

### Alerte

Message distinct de l'alerte de prix, pour que ce soit lisible d'un coup d'œil :

```
📰 Promo repérée — Bangkok en février

« Air France : Bruxelles → Bangkok à partir de 449 € A/R »
Fly4Free · il y a 2 h

Correspond à ta surveillance « Bangkok en février »
Ton meilleur prix suivi : 598 €

🔗 Lire l'annonce
✈️ Vérifier sur Air France
```

---

## Brique 2 — Calendrier des promos

Une page dans l'interface, alimentée par un fichier de configuration éditable.
Deux natures d'entrées, à ne pas mélanger :

**Les rendez-vous réguliers** — prévisibles, on peut les afficher à l'avance :

- **Promo Rewards Flying Blue** : nouvelle sélection chaque mois, réservable
  jusqu'à la fin du mois. C'est le plus régulier et le plus exploitable.
- **Black Friday / Cyber Monday** : fin novembre, la plupart des compagnies.
- **Soldes de janvier** : première quinzaine.

**Les fenêtres irrégulières** — à ne pas afficher comme des dates fermes, mais
comme des liens permanents vers la page offres de chaque compagnie :
Ryanair, Transavia, easyJet, Brussels Airlines, Air France, TUI fly.

Ne pas inventer de dates précises pour les ventes flash : elles ne sont pas
prévisibles. Le calendrier sert à ne pas rater les rendez-vous **réguliers**,
et à offrir un accès en un clic au reste.

Chaque entrée porte un bouton qui ouvre la page dans le navigateur de
l'utilisateur — donc avec sa session, ses miles, ses tarifs membre.

---

## Brique 3 — Boutons « vérifier chez la compagnie »

À ajouter sous **chaque alerte de prix**, indépendamment du module promos.

Pour chaque compagnie pertinente, construire une URL de recherche pré-remplie
avec origine, destination et dates. Aucun identifiant n'est stocké : c'est le
navigateur de l'utilisateur qui porte la session.

Le mécanisme existe déjà dans le projet — voir `booking_url` dans
`watcher/providers/ryanair.py` et `google_flights.py`. Il s'agit de généraliser :
une petite table `compagnie → gabarit d'URL`, et les boutons correspondants.

Vérifier chaque gabarit à la main une fois : les compagnies changent leurs
paramètres d'URL sans prévenir. Prévoir un repli sur la page d'accueil de la
compagnie si le gabarit ne produit rien d'exploitable.

---

## Modèle de données

```sql
CREATE TABLE feed_items (
  id TEXT PRIMARY KEY,        -- hash de l'URL de l'entrée
  source TEXT,
  title TEXT,
  url TEXT,
  published_at TEXT,
  seen_at TEXT,
  places TEXT,                -- lieux extraits, JSON
  matched_watch_id TEXT,      -- NULL si aucune correspondance
  notified INTEGER DEFAULT 0
);
CREATE INDEX idx_feed_published ON feed_items(published_at);
```

Purger au-delà de 90 jours : ces entrées n'ont aucune valeur historique.

---

## Ordre de travail suggéré

1. Brique 3 (boutons compagnies) — la plus rapide, valeur immédiate.
2. Brique 1 avec **un seul flux**, Fly4Free, et une seule destination surveillée.
   Vérifier la qualité du recoupement avant d'en ajouter.
3. Élargir les flux une fois le filtrage anti-bruit éprouvé.
4. Brique 2 (calendrier) en dernier : c'est de l'affichage, pas de la détection.

Le piège de ce module, c'est le **bruit**. Une alerte promo sur trois qui ne
correspond à rien, et l'utilisateur cesse de les lire — ce qui dévalue aussi
les vraies alertes de prix. Mieux vaut un filtrage trop strict au début, quitte
à l'assouplir ensuite.

---

## Ce que ce module ne fera pas

- **Lire les prix derrière un compte compagnie.** Pas d'identifiants stockés,
  jamais. L'utilisateur clique et voit lui-même.
- **Détecter les tarifs NDC dynamiques.** Ils n'existent qu'au moment de la
  requête chez la compagnie ; aucun flux ne les annonce.
- **Garantir l'exhaustivité.** Une promo non relayée par les sites suivis
  passera inaperçue. C'est un filet supplémentaire, pas un remplacement de la
  surveillance tarifaire.
