/**
 * Calendrier des promos — brique 2 de PLANPROMOS.md.
 *
 * Deux natures d'entrées, jamais mélangées :
 *
 *   Les rendez-vous RÉGULIERS, prévisibles, qu'on peut annoncer à l'avance.
 *   Les FENÊTRES IRRÉGULIÈRES, qui n'ont pas de date : les ventes flash ne se
 *   prévoient pas. Leur donner une fausse date ferait rater les vraies. Elles
 *   ne sont donc que des liens permanents vers la page offres de chacun.
 *
 * Format : genre|règle|titre|note|url
 *   mensuel     — reconduit chaque mois, court jusqu'à la fin du mois
 *   regle       — date calculée : black-friday
 *   fenetre     — MM-JJ:MM-JJ, période approximative assumée
 *   permanent   — aucun calendrier, juste un accès direct
 *
 * Codes de vérification au 01/09/2026 : 200 = répond ; 403 = le chemin existe
 * mais le site refuse un client scripté, un navigateur passe ; les chemins en
 * 404 ont été écartés et remplacés par l'accueil.
 */

export default [
  "mensuel||Promo Rewards Flying Blue|Nouvelle sélection de destinations à miles réduits chaque mois, réservable jusqu'à la fin du mois. Le rendez-vous le plus régulier et le plus exploitable.|https://www.flyingblue.com/",
  "regle|black-friday|Black Friday et Cyber Monday|La plupart des compagnies européennes ouvrent des ventes ces jours-là.|https://www.ryanair.com/be/fr/offres",
  "fenetre|01-01:01-15|Soldes de janvier|Première quinzaine, chez la plupart des compagnies. Période approximative : ces ventes n'ont pas de date officielle.|https://www.brusselsairlines.com/fr/fr/homepage/offres",

  "permanent||Ryanair|Page offres — vérifié, répond|https://www.ryanair.com/be/fr/offres",
  "permanent||Transavia|Page offres — chemin existant, protégé contre les robots|https://www.transavia.com/fr-FR/offres/",
  "permanent||Brussels Airlines|Page offres — chemin existant, protégé contre les robots|https://www.brusselsairlines.com/fr/fr/homepage/offres",
  "permanent||TUI fly|Promotions — chemin existant, protégé contre les robots|https://www.tuifly.be/fr/promotions",
  "permanent||easyJet|Vols pas chers — chemin existant, protégé contre les robots|https://www.easyjet.com/fr/vols-pas-chers",
  "permanent||Wizz Air|Accueil — la page offres renvoie 404, on ouvre l'accueil|https://wizzair.com/fr-fr",
  "permanent||Air France|Accueil — site injoignable depuis un serveur, non vérifiable ici|https://www.airfrance.fr/",
];
