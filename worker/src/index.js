/**
 * flight-watcher — Worker Cloudflare.
 *
 * Sert l'interface, l'API du navigateur (session par cookie signé) et l'API du
 * moteur Python tournant sur GitHub Actions (jeton partagé AGENT_TOKEN).
 * La base D1 est la source de vérité des surveillances ; `watches.yaml` ne sert
 * plus que de secours quand le Worker est injoignable.
 */

import UI from './ui.html';
import AIRPORTS from './airports.js';

const SESSION_COOKIE = 'fw_session';
const SESSION_TTL_S = 60 * 60 * 24 * 90;      // 90 jours : « mémorisé sur l'appareil »

const DEFAULT_SETTINGS = {
  cooldown_hours: 12,
  renotify_drop_pct: 5,
  drop_pct: 15,
  drop_min_samples: 8,
  flex_mode: 'shift',
  max_queries_per_run: 120,
  workers: 3,
};

/* ------------------------------------------------------------------ outils */

const enc = new TextEncoder();
const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', ...headers },
  });

const fail = (status, message) => json({ error: message }, status);

const b64 = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const b64url = (s) => s.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64url = (s) => atob(s.replace(/-/g, '+').replace(/_/g, '/'));

/** Comparaison à temps constant : évite de fuiter le secret octet par octet. */
function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

async function hmac(secret, message) {
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return b64url(b64(await crypto.subtle.sign('HMAC', key, enc.encode(message))));
}

/** Vérifie un mot de passe contre `pbkdf2$<iter>$<salt_b64>$<hash_b64>`. */
async function verifyPassword(password, stored) {
  const parts = String(stored || '').split('$');
  if (parts.length !== 4 || parts[0] !== 'pbkdf2') return false;
  const iterations = Number(parts[1]);
  if (!Number.isFinite(iterations) || iterations < 1000) return false;
  const key = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'PBKDF2', salt: unb64(parts[2]), iterations, hash: 'SHA-256' }, key, 256);
  return timingSafeEqual(b64(bits), parts[3]);
}

async function makeSession(secret) {
  const payload = b64url(btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + SESSION_TTL_S })));
  return `${payload}.${await hmac(secret, payload)}`;
}

async function readSession(request, secret) {
  const raw = (request.headers.get('cookie') || '')
    .split(';').map((c) => c.trim())
    .find((c) => c.startsWith(`${SESSION_COOKIE}=`));
  if (!raw) return null;
  const [payload, sig] = raw.slice(SESSION_COOKIE.length + 1).split('.');
  if (!payload || !sig) return null;
  if (!timingSafeEqual(sig, await hmac(secret, payload))) return null;
  try {
    const data = JSON.parse(unb64url(payload));
    return data.exp > Math.floor(Date.now() / 1000) ? data : null;
  } catch {
    return null;
  }
}

const sessionCookie = (value, maxAge) =>
  `${SESSION_COOKIE}=${value}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${maxAge}`;

/* ------------------------------------------------- validation d'une surveillance */

const IATA = /^[A-Z]{3}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;

function codes(value, field) {
  const list = (Array.isArray(value) ? value : String(value ?? '').split(/[,;\s]+/))
    .map((v) => String(v).trim().toUpperCase()).filter(Boolean);
  if (!list.length) throw new HttpError(400, `${field} : au moins un code IATA est requis`);
  for (const c of list) if (!IATA.test(c)) throw new HttpError(400, `${field} : « ${c} » n'est pas un code IATA`);
  return [...new Set(list)];
}

function isoDate(value, field, { optional = false } = {}) {
  if (value === null || value === undefined || value === '') {
    if (optional) return null;
    throw new HttpError(400, `${field} est requis`);
  }
  const s = String(value).trim();
  if (!DATE.test(s) || Number.isNaN(Date.parse(`${s}T00:00:00Z`))) {
    throw new HttpError(400, `${field} : date invalide (attendu AAAA-MM-JJ)`);
  }
  return s;
}

function num(value, field, { min = null, max = null, integer = false, optional = true } = {}) {
  if (value === null || value === undefined || value === '') {
    if (optional) return null;
    throw new HttpError(400, `${field} est requis`);
  }
  const n = Number(value);
  if (!Number.isFinite(n)) throw new HttpError(400, `${field} : nombre attendu`);
  if (integer && !Number.isInteger(n)) throw new HttpError(400, `${field} : entier attendu`);
  if (min !== null && n < min) throw new HttpError(400, `${field} : minimum ${min}`);
  if (max !== null && n > max) throw new HttpError(400, `${field} : maximum ${max}`);
  return n;
}

class HttpError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const slugify = (s) =>
  String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 48);

function watchFromBody(body, existing = null) {
  const get = (k, dflt) => (body[k] === undefined ? (existing ? existing[k] : dflt) : body[k]);

  const origins = body.origins === undefined && existing ? JSON.parse(existing.origins) : codes(body.origins, 'origins');
  const destinations = body.destinations === undefined && existing
    ? JSON.parse(existing.destinations) : codes(body.destinations, 'destinations');
  const depart = body.depart === undefined && existing ? existing.depart : isoDate(body.depart, 'depart');
  let ret = body.ret === undefined && existing ? existing.ret : isoDate(body.ret, 'ret', { optional: true });
  if (ret && ret < depart) throw new HttpError(400, 'La date de retour précède celle de l’aller');

  // Séjour souple : le retour se déduit de la durée. On efface la date fixe
  // pour qu'il n'existe jamais deux définitions concurrentes du retour.
  const nightsMin = body.nights_min === undefined && existing
    ? existing.nights_min : num(body.nights_min, 'nights_min', { min: 1, max: 60, integer: true });
  const nightsMax = body.nights_max === undefined && existing
    ? existing.nights_max : num(body.nights_max, 'nights_max', { min: 1, max: 60, integer: true });
  if (nightsMax != null && nightsMin == null) {
    throw new HttpError(400, 'nights_max sans nights_min : indiquez une durée minimale');
  }
  if (nightsMin != null && nightsMax != null && nightsMax < nightsMin) {
    throw new HttpError(400, 'La durée maximale de séjour est inférieure à la minimale');
  }
  if (nightsMin != null) ret = null;

  const pax = body.passengers === undefined && existing
    ? JSON.parse(existing.passengers)
    : {
        adults: num(body.passengers?.adults ?? 1, 'adults', { min: 1, max: 9, integer: true, optional: false }),
        children: num(body.passengers?.children ?? 0, 'children', { min: 0, max: 8, integer: true, optional: false }),
        infants_in_seat: num(body.passengers?.infants_in_seat ?? 0, 'infants_in_seat', { min: 0, max: 8, integer: true, optional: false }),
        infants_on_lap: num(body.passengers?.infants_on_lap ?? 0, 'infants_on_lap', { min: 0, max: 8, integer: true, optional: false }),
      };

  const providers = body.providers === undefined && existing
    ? JSON.parse(existing.providers)
    : (Array.isArray(body.providers) && body.providers.length ? body.providers : ['google_flights'])
        .map(String).filter((p) => ['google_flights', 'ryanair', 'wizzair'].includes(p));
  if (!providers.length) throw new HttpError(400, 'providers : aucun fournisseur connu');

  const seat = String(get('seat', 'economy'));
  if (!['economy', 'premium-economy', 'business', 'first'].includes(seat)) {
    throw new HttpError(400, `seat : « ${seat} » inconnu`);
  }

  const label = String(get('label', '')).trim().slice(0, 120);
  const id = existing
    ? existing.id
    : (slugify(body.id || label || `${origins[0]}-${destinations[0]}-${depart}`)
       || `watch-${Date.now().toString(36)}`);

  return {
    id,
    label,
    origins: JSON.stringify(origins),
    destinations: JSON.stringify(destinations),
    depart,
    ret,
    threshold: body.threshold === undefined && existing
      ? existing.threshold : num(body.threshold, 'threshold', { min: 0, max: 100000 }),
    currency: String(get('currency', 'EUR')).toUpperCase().slice(0, 3),
    seat,
    max_stops: body.max_stops === undefined && existing
      ? existing.max_stops : num(body.max_stops, 'max_stops', { min: 0, max: 3, integer: true }),
    flex_days: body.flex_days === undefined && existing
      ? existing.flex_days : (num(body.flex_days, 'flex_days', { min: 0, max: 7, integer: true }) ?? 0),
    flex_days_ret: body.flex_days_ret === undefined && existing
      ? existing.flex_days_ret : num(body.flex_days_ret, 'flex_days_ret', { min: 0, max: 7, integer: true }),
    nights_min: nightsMin ?? null,
    nights_max: nightsMax ?? null,
    passengers: JSON.stringify(pax),
    providers: JSON.stringify(providers),
    enabled: Number(Boolean(get('enabled', true) === true || get('enabled', true) === 1)),
    alert_on_drop: Number(Boolean(get('alert_on_drop', true) === true || get('alert_on_drop', true) === 1)),
    notes: String(get('notes', '')).slice(0, 500),
  };
}

/** Ligne D1 → objet consommé par l'interface et par le moteur Python. */
const rowToWatch = (r) => ({
  id: r.id,
  label: r.label,
  origins: JSON.parse(r.origins),
  destinations: JSON.parse(r.destinations),
  depart: r.depart,
  ret: r.ret,
  threshold: r.threshold,
  currency: r.currency,
  seat: r.seat,
  max_stops: r.max_stops,
  flex_days: r.flex_days,
  flex_days_ret: r.flex_days_ret,
  nights_min: r.nights_min,
  nights_max: r.nights_max,
  passengers: JSON.parse(r.passengers),
  providers: JSON.parse(r.providers),
  enabled: Boolean(r.enabled),
  alert_on_drop: Boolean(r.alert_on_drop),
  notes: r.notes,
  created_at: r.created_at,
  updated_at: r.updated_at,
});

/* ------------------------------------------------------------------ réglages */

async function loadSettings(env) {
  const { results } = await env.DB.prepare('SELECT key, value FROM settings').all();
  const out = { ...DEFAULT_SETTINGS };
  for (const row of results || []) {
    if (row.key.includes(':')) continue;          // « meta:last_run » et consorts
    try { out[row.key] = JSON.parse(row.value); } catch { out[row.key] = row.value; }
  }
  return out;
}

/* --------------------------------------------------------------------- API */

async function handleApi(request, env, url, path) {
  const method = request.method;
  const body = ['POST', 'PATCH', 'PUT'].includes(method)
    ? await request.json().catch(() => { throw new HttpError(400, 'Corps JSON invalide'); })
    : {};

  /* --- API du moteur (GitHub Actions) : jeton partagé, pas de cookie --- */
  if (path.startsWith('/api/agent/')) {
    const auth = request.headers.get('authorization') || '';
    const token = auth.startsWith('Bearer ') ? auth.slice(7) : '';
    if (!env.AGENT_TOKEN || !timingSafeEqual(token, env.AGENT_TOKEN)) return fail(401, 'Jeton invalide');

    if (path === '/api/agent/watches' && method === 'GET') {
      // Toutes les surveillances, y compris en pause : le moteur filtre déjà
      // sur `enabled`, et les commandes Telegram relisent puis réécrivent la
      // liste entière — en masquer une reviendrait à la supprimer.
      const { results } = await env.DB.prepare(
        'SELECT * FROM watches ORDER BY created_at').all();
      return json({ settings: await loadSettings(env), watches: (results || []).map(rowToWatch) });
    }

    if (path === '/api/agent/results' && method === 'POST') return agentResults(env, body);
    if (path === '/api/agent/watches' && method === 'PUT') return agentReplaceWatches(env, body);

    // Veille éditoriale. Les entrées arrivent en bloc à chaque passage ; la
    // clé primaire absorbe les republications, très fréquentes d'un flux à
    // l'autre. Seule la correspondance peut évoluer — une entrée déjà vue
    // peut matcher après l'ajout d'une surveillance.
    if (path === '/api/agent/feed' && method === 'POST') {
      const items = Array.isArray(body.items) ? body.items : [];
      if (!items.length) return json({ ok: true, recus: 0 });
      const ts = nowIso();
      const stmts = items.slice(0, 200).map((it) => env.DB.prepare(
        `INSERT INTO feed_items (id, source, title, url, published_at, seen_at, places,
                                 matched_watch_id, reason, notified)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)
         ON CONFLICT(id) DO UPDATE SET
           matched_watch_id = COALESCE(excluded.matched_watch_id, matched_watch_id),
           reason = COALESCE(excluded.reason, reason),
           notified = MAX(notified, excluded.notified)`
      ).bind(String(it.id || '').slice(0, 64), String(it.source || '').slice(0, 80),
             String(it.title || '').slice(0, 400), String(it.url || '').slice(0, 600),
             it.published_at ?? null, ts, JSON.stringify(it.places || {}),
             it.matched_watch_id ?? null, it.reason ?? null, Number(Boolean(it.notified))));

      // Purge : ces entrées n'ont aucune valeur historique.
      stmts.push(env.DB.prepare(
        "DELETE FROM feed_items WHERE seen_at < ?1"
      ).bind(new Date(Date.now() - 90 * 86400000).toISOString()));

      await env.DB.batch(stmts);
      return json({ ok: true, recus: items.length });
    }

    // Sauvegarde : la configuration et l'état d'alerte sont irremplaçables et
    // tiennent en quelques kilo-octets ; l'historique est volumineux, donc
    // versé par tranches. Le dépôt Git en garde toutes les versions, si bien
    // que leur union couvre la totalité sans qu'aucun fichier n'enfle.
    if (path === '/api/agent/export' && method === 'GET') {
      const days = Math.min(Math.max(Number(url.searchParams.get('days')) || 1, 1), 400);
      const since = new Date(Date.now() - days * 86400000).toISOString();
      const [watches, alertState, settings, history] = await Promise.all([
        env.DB.prepare('SELECT * FROM watches ORDER BY created_at').all(),
        env.DB.prepare('SELECT * FROM alert_state').all(),
        env.DB.prepare('SELECT key, value FROM settings').all(),
        env.DB.prepare('SELECT * FROM history WHERE checked_at >= ?1 ORDER BY checked_at').bind(since).all(),
      ]);
      return json({
        exported_at: nowIso(),
        history_since: since,
        watches: watches.results || [],
        alert_state: alertState.results || [],
        settings: settings.results || [],
        history: history.results || [],
      });
    }

    return fail(404, 'Route inconnue');
  }

  /* --- Entrée pour un service de ping externe : un GET, un jeton dans
         l'URL, aucun en-tête à configurer. Le garde-fou d'ancienneté rend
         l'appel idempotent : même martelée, l'adresse ne peut pas déclencher
         plus d'un relevé toutes les 25 minutes. --- */
  const cronMatch = path.match(/^\/api\/cron\/([A-Za-z0-9_-]{16,128})$/);
  if (cronMatch && method === 'GET') {
    if (!env.CRON_TOKEN || !timingSafeEqual(cronMatch[1], env.CRON_TOKEN)) {
      return new Response('Jeton invalide\n', { status: 401, headers: { 'content-type': 'text/plain; charset=utf-8' } });
    }
    const outcome = await runScheduled(env);
    // Un relevé qui ne tombe plus depuis deux heures se signale par un code
    // d'erreur — le service de ping le voit comme une panne — et par un
    // message Telegram. Les deux survivent à l'arrêt du moteur.
    const age = await minutesSinceLastRun(env);
    await watchdog(env, age);
    const broken = age === null || age > ALERT_MINUTES;
    return new Response(
      broken
        ? `PANNE — aucun relevé depuis ${age === null ? 'toujours' : Math.round(age) + ' min'}. ${outcome}\n`
        : `${outcome}\n`,
      {
        status: broken ? 503 : 200,
        headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' },
      });
  }

  /* --- API du navigateur : session obligatoire sauf /api/login --- */
  if (path === '/api/login' && method === 'POST') {
    if (!env.APP_PASSWORD_HASH || !env.SESSION_SECRET) return fail(500, 'Worker non configuré');
    if (!(await verifyPassword(String(body.password || ''), env.APP_PASSWORD_HASH))) {
      return fail(401, 'Mot de passe incorrect');
    }
    const token = await makeSession(env.SESSION_SECRET);
    return json({ ok: true }, 200, { 'set-cookie': sessionCookie(token, SESSION_TTL_S) });
  }

  if (path === '/api/logout' && method === 'POST') {
    return json({ ok: true }, 200, { 'set-cookie': sessionCookie('', 0) });
  }

  if (!(await readSession(request, env.SESSION_SECRET || ''))) return fail(401, 'Session expirée');

  if (path === '/api/state' && method === 'GET') return stateResponse(env);

  // Référentiel figé : le navigateur peut le garder une journée.
  if (path === '/api/airports' && method === 'GET') {
    return json({ airports: AIRPORTS }, 200, { 'cache-control': 'private, max-age=86400' });
  }

  if (path === '/api/feed' && method === 'GET') {
    const limite = Math.min(Math.max(Number(url.searchParams.get('limit')) || 40, 1), 200);
    const { results } = await env.DB.prepare(
      'SELECT id, source, title, url, published_at, places, matched_watch_id, reason'
      // Les correspondances d'abord : dans un fil chronologique, la seule
      // entrée qui vous concerne se retrouvait au 47e rang sur 50.
      + ' FROM feed_items ORDER BY (matched_watch_id IS NULL),'
      + ' COALESCE(published_at, seen_at) DESC LIMIT ?1'
    ).bind(limite).all();
    const suivis = await env.DB.prepare(
      'SELECT COUNT(*) AS n FROM feed_items WHERE matched_watch_id IS NOT NULL').first();
    return json({ items: results || [], correspondances: suivis ? suivis.n : 0 });
  }

  if (path === '/api/history' && method === 'GET') {
    const watchId = url.searchParams.get('watch');
    const days = Math.min(Math.max(Number(url.searchParams.get('days')) || 90, 1), 400);
    const since = new Date(Date.now() - days * 86400000).toISOString();
    const cols = 'watch_id, MIN(price) AS price, currency, checked_at, origin, destination, depart, ret,'
               + ' airlines, stops, booking_url';
    const stmt = watchId
      ? env.DB.prepare(`SELECT ${cols} FROM history WHERE watch_id = ?1 AND checked_at >= ?2`
          + ' GROUP BY watch_id, checked_at ORDER BY checked_at').bind(watchId, since)
      : env.DB.prepare(`SELECT ${cols} FROM history WHERE checked_at >= ?1`
          + ' GROUP BY watch_id, checked_at ORDER BY checked_at').bind(since);
    const { results } = await stmt.all();
    return json({ rows: results || [] });
  }

  // Prix par date de départ : ce que le relevé teste depuis toujours et qu'on
  // jetait. C'est la question « quel jour partir », pas « comment le prix
  // évolue ».
  if (path === '/api/by-date' && method === 'GET') {
    const watchId = url.searchParams.get('watch');
    if (!watchId) throw new HttpError(400, 'paramètre watch requis');
    const days = Math.min(Math.max(Number(url.searchParams.get('days')) || 30, 1), 400);
    const since = new Date(Date.now() - days * 86400000).toISOString();

    const last = await env.DB.prepare(
      'SELECT MAX(checked_at) AS ts FROM history WHERE watch_id = ?1').bind(watchId).first();
    if (!last || !last.ts) return json({ checked_at: null, latest: [], best: [] });

    const [latest, best] = await Promise.all([
      env.DB.prepare(
        'SELECT depart, ret, MIN(price) AS price, origin, destination, currency, booking_url, airlines'
        + ' FROM history WHERE watch_id = ?1 AND checked_at = ?2 GROUP BY depart ORDER BY depart'
      ).bind(watchId, last.ts).all(),
      env.DB.prepare(
        'SELECT depart, MIN(price) AS price FROM history WHERE watch_id = ?1 AND checked_at >= ?2'
        + ' GROUP BY depart ORDER BY depart'
      ).bind(watchId, since).all(),
    ]);
    return json({ checked_at: last.ts, days, latest: latest.results || [], best: best.results || [] });
  }

  if (path === '/api/watches' && method === 'POST') {
    const w = watchFromBody(body);
    const exists = await env.DB.prepare('SELECT id FROM watches WHERE id = ?1').bind(w.id).first();
    if (exists) throw new HttpError(409, `Une surveillance « ${w.id} » existe déjà`);
    const ts = nowIso();
    await env.DB.prepare(
      `INSERT INTO watches (id, label, origins, destinations, depart, ret, threshold, currency, seat,
                            max_stops, flex_days, flex_days_ret, nights_min, nights_max,
                            passengers, providers, enabled, alert_on_drop, notes, created_at, updated_at)
       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?20)`
    ).bind(w.id, w.label, w.origins, w.destinations, w.depart, w.ret, w.threshold, w.currency, w.seat,
           w.max_stops, w.flex_days, w.flex_days_ret, w.nights_min, w.nights_max, w.passengers,
           w.providers, w.enabled, w.alert_on_drop, w.notes, ts).run();
    return json({ ok: true, watch: rowToWatch({ ...w, created_at: ts, updated_at: ts }) }, 201);
  }

  const watchMatch = path.match(/^\/api\/watches\/([A-Za-z0-9._-]{1,64})$/);
  if (watchMatch) {
    const id = watchMatch[1];
    const existing = await env.DB.prepare('SELECT * FROM watches WHERE id = ?1').bind(id).first();
    if (!existing) return fail(404, 'Surveillance inconnue');

    if (method === 'PATCH') {
      const w = watchFromBody(body, existing);
      const ts = nowIso();
      await env.DB.prepare(
        `UPDATE watches SET label=?2, origins=?3, destinations=?4, depart=?5, ret=?6, threshold=?7,
                            currency=?8, seat=?9, max_stops=?10, flex_days=?11, flex_days_ret=?12,
                            nights_min=?13, nights_max=?14, passengers=?15, providers=?16,
                            enabled=?17, alert_on_drop=?18, notes=?19, updated_at=?20
         WHERE id = ?1`
      ).bind(id, w.label, w.origins, w.destinations, w.depart, w.ret, w.threshold, w.currency, w.seat,
             w.max_stops, w.flex_days, w.flex_days_ret, w.nights_min, w.nights_max, w.passengers,
             w.providers, w.enabled, w.alert_on_drop, w.notes, ts).run();
      return json({ ok: true, watch: rowToWatch({ ...w, created_at: existing.created_at, updated_at: ts }) });
    }

    if (method === 'DELETE') {
      await env.DB.batch([
        env.DB.prepare('DELETE FROM history WHERE watch_id = ?1').bind(id),
        env.DB.prepare('DELETE FROM alert_state WHERE watch_id = ?1').bind(id),
        env.DB.prepare('DELETE FROM watches WHERE id = ?1').bind(id),
      ]);
      return json({ ok: true });
    }
    return fail(405, 'Méthode non autorisée');
  }

  if (path === '/api/settings' && method === 'PATCH') {
    const stmts = [];
    for (const [key, value] of Object.entries(body)) {
      if (!(key in DEFAULT_SETTINGS)) continue;
      stmts.push(env.DB.prepare(
        'INSERT INTO settings (key, value) VALUES (?1, ?2) ON CONFLICT(key) DO UPDATE SET value = ?2'
      ).bind(key, JSON.stringify(value)));
    }
    if (stmts.length) await env.DB.batch(stmts);
    return json({ ok: true, settings: await loadSettings(env) });
  }

  if (path === '/api/run' && method === 'POST') return triggerRun(env, body);

  return fail(404, 'Route inconnue');
}

/* ----------------------------------------------------------------- lectures */

// 48 relevés par jour et par surveillance : au-delà d'une semaine, la charge
// utile de /api/state deviendrait lourde à chaque ouverture de page. Les
// fenêtres plus longues passent par /api/history, à la demande.
const STATE_HISTORY_DAYS = 7;

async function stateResponse(env) {
  const since = new Date(Date.now() - STATE_HISTORY_DAYS * 86400000).toISOString();
  const [watches, states, history, settings] = await Promise.all([
    env.DB.prepare('SELECT * FROM watches ORDER BY created_at').all(),
    env.DB.prepare('SELECT * FROM alert_state').all(),
    // Une ligne par relevé, pas par combinaison de dates : depuis qu'on garde
    // toutes les combinaisons, la charge utile brute serait vingt fois plus
    // lourde. SQLite garantit que les colonnes nues d'un GROUP BY avec MIN()
    // proviennent de la ligne qui a produit ce minimum.
    env.DB.prepare(
      'SELECT watch_id, MIN(price) AS price, currency, checked_at, origin, destination, depart, ret,'
      + ' airlines, stops, booking_url FROM history WHERE checked_at >= ?1'
      + ' GROUP BY watch_id, checked_at ORDER BY checked_at').bind(since).all(),
    loadSettings(env),
  ]);

  const byId = Object.fromEntries((states.results || []).map((s) => [s.watch_id, s]));
  const lastRun = await env.DB.prepare("SELECT value FROM settings WHERE key = 'meta:last_run'").first();
  const lastCron = await env.DB.prepare("SELECT value FROM settings WHERE key = 'meta:last_cron'").first();

  return json({
    ran_at: lastRun ? JSON.parse(lastRun.value).ran_at : null,
    last_run: lastRun ? JSON.parse(lastRun.value) : null,
    last_cron: lastCron ? JSON.parse(lastCron.value) : null,
    settings,
    watches: (watches.results || []).map((r) => {
      const s = byId[r.id] || {};
      return {
        ...rowToWatch(r),
        best_price: s.last_price ?? null,
        best_ever: s.best_ever ?? null,
        best_route: s.best_route ?? null,
        booking_url: s.booking_url ?? null,
        status: s.status ?? (r.enabled ? 'pending' : 'paused'),
        last_checked_at: s.last_checked_at ?? null,
        last_alert_at: s.last_alert_at ?? null,
        errors: s.errors ? JSON.parse(s.errors) : [],
      };
    }),
    history: history.results || [],
    history_days: STATE_HISTORY_DAYS,
    alert_after_minutes: ALERT_MINUTES,
  });
}

/* ---------------------------------------------------- écriture par le moteur */

async function agentResults(env, body) {
  const ranAt = typeof body.ran_at === 'string' ? body.ran_at : nowIso();
  const results = Array.isArray(body.results) ? body.results : [];
  const stmts = [];
  let inserted = 0;

  for (const r of results) {
    const watchId = String(r.watch_id || '');
    if (!watchId) continue;

    for (const q of Array.isArray(r.quotes) ? r.quotes : []) {
      if (!Number.isFinite(Number(q.price)) || Number(q.price) <= 0) continue;
      inserted++;
      stmts.push(env.DB.prepare(
        `INSERT OR IGNORE INTO history (watch_id, provider, origin, destination, depart, ret, price,
                                        currency, airlines, stops, duration_min, booking_url, checked_at)
         VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13)`
      ).bind(watchId, String(q.provider || 'unknown'), String(q.origin || ''), String(q.destination || ''),
             String(q.depart || ''), q.ret ?? null, Number(q.price), String(q.currency || 'EUR'),
             JSON.stringify(q.airlines || []), q.stops ?? null, q.duration_min ?? null,
             q.booking_url ?? null, String(q.checked_at || ranAt)));
    }

    const s = r.state || {};
    stmts.push(env.DB.prepare(
      `INSERT INTO alert_state (watch_id, last_price, best_ever, last_alert_price, last_alert_at,
                                last_alert_reason, last_checked_at, status, best_route, booking_url, errors)
       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)
       ON CONFLICT(watch_id) DO UPDATE SET
         last_price=?2,
         -- best_ever ne remonte jamais, et un envoi partiel n'efface ni le meilleur
         -- prix connu ni l'horodatage de la dernière alerte : perdre celui-ci
         -- relancerait le cooldown anti-spam à zéro.
         best_ever=CASE WHEN ?3 IS NULL THEN best_ever
                        WHEN best_ever IS NULL THEN ?3
                        ELSE MIN(best_ever, ?3) END,
         last_alert_price=COALESCE(?4, last_alert_price),
         last_alert_at=COALESCE(?5, last_alert_at),
         last_alert_reason=COALESCE(?6, last_alert_reason),
         last_checked_at=?7, status=?8,
         best_route=COALESCE(?9, best_route),
         booking_url=COALESCE(?10, booking_url),
         errors=?11`
    ).bind(watchId, s.last_price ?? null, s.best_ever ?? null, s.last_alert_price ?? null,
           s.last_alert_at ?? null, s.last_alert_reason ?? null, s.last_checked_at ?? ranAt,
           String(s.status || 'ok'), s.best_route ?? null, s.booking_url ?? null,
           JSON.stringify(r.errors || [])));
  }

  stmts.push(env.DB.prepare(
    "INSERT INTO settings (key, value) VALUES ('meta:last_run', ?1) ON CONFLICT(key) DO UPDATE SET value = ?1"
  ).bind(JSON.stringify({ ran_at: ranAt, watches: results.length, quotes: inserted })));

  if (stmts.length) await env.DB.batch(stmts);
  return json({ ok: true, watches: results.length, quotes: inserted, ran_at: ranAt });
}

/* ------------------------------------------------- relevé immédiat à la demande */

async function triggerRun(env, body) {
  if (!env.GITHUB_TOKEN) return fail(503, 'GITHUB_TOKEN absent : relevé immédiat indisponible');
  const inputs = {};
  if (body.watch) inputs.watch = String(body.watch).slice(0, 64);

  const res = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${env.GITHUB_WORKFLOW}/dispatches`,
    {
      method: 'POST',
      headers: {
        authorization: `Bearer ${env.GITHUB_TOKEN}`,
        accept: 'application/vnd.github+json',
        'user-agent': 'flight-watcher-worker',
        'content-type': 'application/json',
      },
      body: JSON.stringify({ ref: env.GITHUB_REF || 'main', inputs }),
    });

  if (res.status === 204) return json({ ok: true, queued: true });
  return fail(502, `GitHub a refusé le déclenchement (${res.status}) : ${(await res.text()).slice(0, 300)}`);
}

/**
 * Remplace la liste des surveillances — utilisé par les commandes Telegram,
 * qui manipulent la liste entière comme elles le faisaient avec watches.yaml.
 */
async function agentReplaceWatches(env, body) {
  const incoming = Array.isArray(body.watches) ? body.watches : null;
  if (!incoming) throw new HttpError(400, 'watches : liste attendue');

  const { results } = await env.DB.prepare('SELECT * FROM watches').all();
  const known = new Map((results || []).map((r) => [r.id, r]));

  // Une liste vide face à une base peuplée signale bien plus souvent un bug
  // d'appelant qu'une intention. On refuse, sauf demande explicite.
  if (!incoming.length && known.size && !body.allow_empty) {
    throw new HttpError(409, `Liste vide alors que la base contient ${known.size} surveillance(s) — refusé`);
  }

  const ts = nowIso();
  const stmts = [];
  const seen = new Set();

  for (const raw of incoming) {
    const id = slugify(String(raw.id || ''));
    if (!id) throw new HttpError(400, 'chaque surveillance doit porter un id');
    const w = watchFromBody({ ...raw, id });
    seen.add(id);
    const createdAt = known.has(id) ? known.get(id).created_at : ts;
    stmts.push(env.DB.prepare(
      `INSERT INTO watches (id, label, origins, destinations, depart, ret, threshold, currency, seat,
                            max_stops, flex_days, flex_days_ret, nights_min, nights_max,
                            passengers, providers, enabled, alert_on_drop, notes, created_at, updated_at)
       VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20,?21)
       ON CONFLICT(id) DO UPDATE SET
         label=?2, origins=?3, destinations=?4, depart=?5, ret=?6, threshold=?7, currency=?8,
         seat=?9, max_stops=?10, flex_days=?11, flex_days_ret=?12, nights_min=?13, nights_max=?14,
         passengers=?15, providers=?16, enabled=?17, alert_on_drop=?18, notes=?19, updated_at=?21`
    ).bind(id, w.label, w.origins, w.destinations, w.depart, w.ret, w.threshold, w.currency, w.seat,
           w.max_stops, w.flex_days, w.flex_days_ret, w.nights_min, w.nights_max, w.passengers,
           w.providers, w.enabled, w.alert_on_drop, w.notes, createdAt, ts));
  }

  const removed = [...known.keys()].filter((id) => !seen.has(id));
  for (const id of removed) {
    stmts.push(env.DB.prepare('DELETE FROM history WHERE watch_id = ?1').bind(id));
    stmts.push(env.DB.prepare('DELETE FROM alert_state WHERE watch_id = ?1').bind(id));
    stmts.push(env.DB.prepare('DELETE FROM watches WHERE id = ?1').bind(id));
  }

  await env.DB.batch(stmts);
  return json({ ok: true, upserted: seen.size, removed });
}

/* ---------------------------------------------------- relance planifiée */

/** Âge du dernier relevé, en minutes ; null si on n'en a jamais vu. */
async function minutesSinceLastRun(env) {
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = 'meta:last_run'").first();
  if (!row) return null;
  try {
    const ranAt = Date.parse(JSON.parse(row.value).ran_at);
    return Number.isFinite(ranAt) ? (Date.now() - ranAt) / 60000 : null;
  } catch {
    return null;
  }
}

const STALE_MINUTES = 25;      // au-delà, le cron relance
const ALERT_MINUTES = 120;     // au-delà, quelque chose est cassé

/**
 * Ne déclenche un relevé que si GitHub ne l'a pas fait. Tant que son
 * planificateur tient ses créneaux de :00 et :30, ce handler ne fait rien.
 */
async function runScheduled(env) {
  const age = await minutesSinceLastRun(env);
  if (age !== null && age < STALE_MINUTES) {
    await noteCron(env, 'skip', age);
    const message = `Relevé vieux de ${age.toFixed(0)} min — rien à faire.`;
    console.log(message);
    return message;
  }

  const res = await triggerRun(env, {});
  const ok = res.status === 200;
  const message = ok
    ? `Relevé déclenché (dernier il y a ${age === null ? 'jamais' : age.toFixed(0) + ' min'}).`
    : `Déclenchement refusé : ${(await res.clone().text()).slice(0, 200)}`;
  console.log(message);
  await noteCron(env, ok ? 'dispatch' : 'error', age);
  return message;
}

/* --------------------------------------------------------- alerte de panne */

const ALARM_REPEAT_MINUTES = 360;   // ne pas répéter l'alarme avant 6 h

async function telegram(env, text) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return false;
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text,
      parse_mode: 'HTML',
      disable_web_page_preview: true,
    }),
  });
  if (!res.ok) console.error('Telegram a refusé le message :', res.status, (await res.text()).slice(0, 200));
  return res.ok;
}

/**
 * Alerte quand les relevés s'arrêtent, et signale la reprise.
 *
 * L'alarme part du Worker, pas du moteur : quand la chaîne est en panne, le
 * moteur ne tourne précisément plus. Elle ne se répète pas avant six heures —
 * une panne prolongée ne doit pas devenir un harcèlement.
 */
async function watchdog(env, age) {
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = 'meta:alarm'").first();
  let alarm = null;
  try { alarm = row ? JSON.parse(row.value) : null; } catch { alarm = null; }

  const broken = age === null || age > ALERT_MINUTES;
  const save = (value) => env.DB.prepare(
    "INSERT INTO settings (key, value) VALUES ('meta:alarm', ?1) ON CONFLICT(key) DO UPDATE SET value = ?1"
  ).bind(JSON.stringify(value)).run();

  if (!broken) {
    if (alarm) {
      await telegram(env, '✅ <b>Les relevés ont repris.</b>\n\nLa surveillance est de nouveau à jour.');
      await env.DB.prepare("DELETE FROM settings WHERE key = 'meta:alarm'").run();
    }
    return;
  }

  const since = alarm && alarm.last_sent ? (Date.now() - Date.parse(alarm.last_sent)) / 60000 : Infinity;
  if (since < ALARM_REPEAT_MINUTES) return;

  const duree = age === null ? 'aucun relevé n’a jamais abouti'
    : `dernier relevé il y a ${age < 120 ? Math.round(age) + ' minutes' : Math.round(age / 60) + ' heures'}`;
  const sent = await telegram(env,
    `🚨 <b>La surveillance s’est arrêtée</b>\n\n${duree}.\n`
    + 'Les prix affichés ne sont plus à jour. Le déclencheur externe ou GitHub Actions est en cause.');
  await save({ last_sent: sent ? nowIso() : (alarm && alarm.last_sent) || null, since: (alarm && alarm.since) || nowIso() });
}

async function noteCron(env, action, age) {
  await env.DB.prepare(
    "INSERT INTO settings (key, value) VALUES ('meta:last_cron', ?1) ON CONFLICT(key) DO UPDATE SET value = ?1"
  ).bind(JSON.stringify({ at: nowIso(), action, age_min: age === null ? null : Math.round(age) })).run();
}

/* ------------------------------------------------------------------- entrée */

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(runScheduled(env).then(() => minutesSinceLastRun(env)).then((age) => watchdog(env, age)));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, '') || '/';

    try {
      if (path.startsWith('/api/')) return await handleApi(request, env, url, path);
      if (path === '/' && request.method === 'GET') {
        return new Response(UI, { headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-cache' } });
      }
      return new Response('Not found', { status: 404 });
    } catch (err) {
      if (err instanceof HttpError) return fail(err.status, err.message);
      console.error(err);
      return fail(500, 'Erreur interne');
    }
  },
};
