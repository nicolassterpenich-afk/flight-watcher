-- Schéma D1 du flight-watcher.
-- Idempotent : on peut le rejouer sans perdre de données.

CREATE TABLE IF NOT EXISTS watches (
  id            TEXT PRIMARY KEY,
  label         TEXT NOT NULL DEFAULT '',
  origins       TEXT NOT NULL,              -- JSON: ["BRU","CRL"]
  destinations  TEXT NOT NULL,              -- JSON: ["BKK"]
  depart        TEXT NOT NULL,              -- YYYY-MM-DD
  ret           TEXT,                       -- YYYY-MM-DD, NULL = aller simple
  threshold     REAL,
  currency      TEXT NOT NULL DEFAULT 'EUR',
  seat          TEXT NOT NULL DEFAULT 'economy',
  max_stops     INTEGER,
  flex_days     INTEGER NOT NULL DEFAULT 0,   -- ± N jours autour de l'aller
  flex_days_ret INTEGER,                      -- ± N jours autour du retour ; NULL = comme l'aller
  nights_min    INTEGER,                      -- séjour souple : « 7 à 10 nuits »
  nights_max    INTEGER,                      -- le retour est alors calculé, ret reste NULL
  passengers    TEXT NOT NULL DEFAULT '{"adults":1,"children":0,"infants_in_seat":0,"infants_on_lap":0}',
  providers     TEXT NOT NULL DEFAULT '["google_flights"]',
  enabled       INTEGER NOT NULL DEFAULT 1,
  alert_on_drop INTEGER NOT NULL DEFAULT 1,
  notes         TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS history (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  watch_id     TEXT NOT NULL,
  provider     TEXT NOT NULL,
  origin       TEXT NOT NULL,
  destination  TEXT NOT NULL,
  depart       TEXT NOT NULL,
  ret          TEXT,
  price        REAL NOT NULL,
  currency     TEXT NOT NULL DEFAULT 'EUR',
  airlines     TEXT NOT NULL DEFAULT '[]',
  stops        INTEGER,
  duration_min INTEGER,
  booking_url  TEXT,
  checked_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_watch ON history(watch_id, checked_at);
-- Un relevé garde désormais le meilleur prix de CHAQUE combinaison de dates :
-- la clé d'unicité doit donc inclure le retour, sinon deux combinaisons qui ne
-- diffèrent que par lui s'écraseraient. ifnull() parce qu'en SQLite deux NULL
-- sont considérés distincts dans un index unique — les allers simples ne
-- seraient jamais dédoublonnés.
CREATE UNIQUE INDEX IF NOT EXISTS idx_history_unique
  ON history(watch_id, checked_at, provider, origin, destination, depart, ifnull(ret, ''));
CREATE INDEX IF NOT EXISTS idx_history_depart ON history(watch_id, depart, price);

CREATE TABLE IF NOT EXISTS alert_state (
  watch_id          TEXT PRIMARY KEY,
  last_price        REAL,
  best_ever         REAL,
  last_alert_price  REAL,
  last_alert_at     TEXT,
  last_alert_reason TEXT,
  last_checked_at   TEXT,
  status            TEXT,
  best_route        TEXT,
  booking_url       TEXT,
  errors            TEXT NOT NULL DEFAULT '[]'
);

-- Réglages globaux (cooldown, drop_pct…) + méta du dernier run.
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
