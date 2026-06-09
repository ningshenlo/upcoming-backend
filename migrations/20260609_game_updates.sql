CREATE TABLE IF NOT EXISTS game_updates (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  game_id text NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  release_event_id text REFERENCES release_events(id) ON DELETE SET NULL,
  store_link_id text REFERENCES store_links(id) ON DELETE SET NULL,
  source_id text REFERENCES sources(id) ON DELETE SET NULL,
  data_job_id text REFERENCES data_jobs(id) ON DELETE SET NULL,
  source_slug text NOT NULL,
  source_url text,
  update_type text NOT NULL CHECK (
    update_type = ANY (ARRAY[
      'release_date_announced',
      'release_date_changed',
      'release_date_confirmed',
      'demo_available',
      'demo_removed',
      'price_available',
      'price_changed',
      'metadata_enriched',
      'metadata_changed',
      'company_changed'
    ]::text[])
  ),
  title text NOT NULL,
  summary text NOT NULL,
  before_value jsonb NOT NULL DEFAULT '{}'::jsonb,
  after_value jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence integer NOT NULL DEFAULT 70,
  raw_data_path text,
  dedupe_key text NOT NULL UNIQUE,
  observed_at timestamp with time zone NOT NULL DEFAULT now(),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT game_updates_confidence_check CHECK (confidence >= 0 AND confidence <= 100)
);

CREATE INDEX IF NOT EXISTS game_updates_game_observed_idx ON game_updates (game_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS game_updates_type_idx ON game_updates (update_type);
CREATE INDEX IF NOT EXISTS game_updates_data_job_id_idx ON game_updates (data_job_id);
