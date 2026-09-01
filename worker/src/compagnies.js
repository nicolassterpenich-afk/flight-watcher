/**
 * Où aller vérifier un prix soi-même — brique 3 de PLANPROMOS.md.
 *
 * Trois niveaux, dans l'ordre de fiabilité :
 *
 *   meta  moteur de recherche, valable sur toutes les routes. Gabarits
 *         vérifiés par requête : ils répondent 200, aller simple comme A/R.
 *   deep  compagnie avec gabarit pré-rempli, éprouvé en production par le
 *         projet — ce sont les booking_url que les fournisseurs fabriquent
 *         déjà et que vous ouvrez depuis les alertes.
 *   home  compagnie sans gabarit vérifiable. Leurs sites répondent 403 à une
 *         requête scriptée, ou rien du tout : impossible d'éprouver un lien
 *         profond d'ici sans risquer d'en livrer un qui tombe en 404. On
 *         ouvre l'accueil, comme la spec l'autorise.
 *
 * Jetons disponibles : {origin} {destination} {depart} {ret} {adults}
 * {origin_l} {destination_l} en minuscules, {depart_c} {ret_c} en AAMMJJ.
 */

export default [
  "meta|Skyscanner|https://www.skyscanner.fr/transport/vols/{origin_l}/{destination_l}/{depart_c}/{ret_c}/",
  "meta|Kayak|https://www.kayak.fr/flights/{origin}-{destination}/{depart}/{ret}",

  "deep|Ryanair|https://www.ryanair.com/be/fr/trip/flights/select?adults={adults}&teens=0&children=0&infants=0&dateOut={depart}&dateIn={ret}&isConnectedFlight=false&isReturn={est_ar}&discount=0&originIata={origin}&destinationIata={destination}",
  "deep|Wizz Air|https://wizzair.com/en-gb/booking/select-flight/{origin}/{destination}/{depart}/{ret_ou_null}/{adults}/0/0/null",

  "home|Air France|https://www.airfrance.fr/",
  "home|KLM|https://www.klm.fr/",
  "home|Brussels Airlines|https://www.brusselsairlines.com/fr/fr/",
  "home|Lufthansa|https://www.lufthansa.com/fr/fr/homepage",
  "home|Lufthansa City Airlines|https://www.lufthansa.com/fr/fr/homepage",
  "home|Austrian|https://www.austrian.com/fr/fr/homepage",
  "home|Swiss|https://www.swiss.com/fr/fr/homepage",
  "home|British Airways|https://www.britishairways.com/travel/home/public/fr_fr/",
  "home|Iberia|https://www.iberia.com/fr/",
  "home|Vueling|https://www.vueling.com/fr",
  "home|easyJet|https://www.easyjet.com/fr",
  "home|Transavia|https://www.transavia.com/fr-FR/accueil/",
  "home|TUI fly|https://www.tuifly.be/fr",
  "home|Corsair|https://www.corsair.fr/",
  "home|Air Caraïbes|https://www.aircaraibes.com/",
  "home|Emirates|https://www.emirates.com/fr/french/",
  "home|Qatar Airways|https://www.qatarairways.com/fr-fr/homepage.html",
  "home|Etihad|https://www.etihad.com/fr-fr/",
  "home|Turkish Airlines|https://www.turkishairlines.com/fr-fr/",
  "home|flydubai|https://www.flydubai.com/fr/",
  "home|Oman Air|https://www.omanair.com/fr",
  "home|Finnair|https://www.finnair.com/fr-fr",
  "home|Condor|https://www.condor.com/fr/",
  "home|Luxair|https://www.luxair.lu/fr",
  "home|Hainan|https://www.hainanairlines.com/",
  "home|Singapore Airlines|https://www.singaporeair.com/fr_FR/fr/home",
  "home|Thai Airways|https://www.thaiairways.com/fr_FR/index.page",
  "home|China Southern|https://global.csair.com/FR/FR/Home",
  "home|China Eastern|https://fr.ceair.com/",
  "home|Air China|https://www.airchina.fr/",
  "home|Saudia|https://www.saudia.com/fr",
  "home|Air Belgium|https://www.airbelgium.com/fr",
];
