CREATE TABLE IF NOT EXISTS source_observations (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  game_id text NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  release_event_id text REFERENCES release_events(id) ON DELETE SET NULL,
  store_link_id text REFERENCES store_links(id) ON DELETE SET NULL,
  source_id text REFERENCES sources(id) ON DELETE SET NULL,
  data_job_id text REFERENCES data_jobs(id) ON DELETE SET NULL,
  source_slug text NOT NULL,
  source_url text,
  raw_data_path text,
  fact_type text NOT NULL,
  fact_key text NOT NULL,
  observed_value jsonb NOT NULL DEFAULT '{}'::jsonb,
  normalized_value jsonb NOT NULL DEFAULT '{}'::jsonb,
  confidence integer NOT NULL DEFAULT 50,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  observed_at timestamp with time zone NOT NULL DEFAULT now(),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT source_observations_fact_type_check CHECK (
    fact_type = ANY (ARRAY[
      'entity_profile',
      'event',
      'release_date',
      'launch_time',
      'demo_availability',
      'preorder_availability',
      'wishlist_availability',
      'price',
      'store_link',
      'store_metadata'
    ]::text[])
  ),
  CONSTRAINT source_observations_confidence_check CHECK (confidence >= 0 AND confidence <= 100)
);

CREATE INDEX IF NOT EXISTS source_observations_game_id_idx ON source_observations (game_id);
CREATE INDEX IF NOT EXISTS source_observations_release_event_id_idx ON source_observations (release_event_id);
CREATE INDEX IF NOT EXISTS source_observations_store_link_id_idx ON source_observations (store_link_id);
CREATE INDEX IF NOT EXISTS source_observations_source_id_idx ON source_observations (source_id);
CREATE INDEX IF NOT EXISTS source_observations_data_job_id_idx ON source_observations (data_job_id);
CREATE INDEX IF NOT EXISTS source_observations_fact_idx ON source_observations (fact_type, fact_key);
CREATE INDEX IF NOT EXISTS source_observations_observed_at_idx ON source_observations (observed_at DESC);
