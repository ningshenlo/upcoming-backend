CREATE TABLE IF NOT EXISTS alerts (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  alert_type text NOT NULL,
  severity text NOT NULL DEFAULT 'warning',
  title text NOT NULL,
  description text NOT NULL,
  game_id text REFERENCES games(id) ON DELETE CASCADE,
  release_event_id text REFERENCES release_events(id) ON DELETE SET NULL,
  source_id text REFERENCES sources(id) ON DELETE SET NULL,
  signal_id text REFERENCES signals(id) ON DELETE SET NULL,
  data_job_id text REFERENCES data_jobs(id) ON DELETE SET NULL,
  conflict_detail jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'open',
  resolved_by text,
  resolved_at timestamp with time zone,
  resolution_note text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

ALTER TABLE alerts ADD COLUMN IF NOT EXISTS alert_type text NOT NULL DEFAULT 'source_failure';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS severity text NOT NULL DEFAULT 'warning';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS title text NOT NULL DEFAULT 'Alert';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS game_id text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS release_event_id text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS source_id text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS signal_id text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS data_job_id text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS conflict_detail jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'open';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_by text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolved_at timestamp with time zone;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS resolution_note text;
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS created_at timestamp with time zone NOT NULL DEFAULT now();
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();

UPDATE alerts SET alert_type = 'source_failure' WHERE alert_type IS NULL;
UPDATE alerts SET severity = 'warning' WHERE severity IS NULL;
UPDATE alerts SET title = 'Alert' WHERE title IS NULL;
UPDATE alerts SET description = '' WHERE description IS NULL;
UPDATE alerts SET conflict_detail = '{}'::jsonb WHERE conflict_detail IS NULL;
UPDATE alerts SET status = 'open' WHERE status IS NULL;
UPDATE alerts SET created_at = now() WHERE created_at IS NULL;
UPDATE alerts SET updated_at = now() WHERE updated_at IS NULL;

ALTER TABLE alerts ALTER COLUMN alert_type SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN severity SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN title SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN description SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN conflict_detail SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN status SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN created_at SET NOT NULL;
ALTER TABLE alerts ALTER COLUMN updated_at SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_game_id_fkey'
  ) THEN
    ALTER TABLE alerts
      ADD CONSTRAINT alerts_game_id_fkey
      FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
      NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_release_event_id_fkey'
  ) THEN
    ALTER TABLE alerts
      ADD CONSTRAINT alerts_release_event_id_fkey
      FOREIGN KEY (release_event_id) REFERENCES release_events(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_source_id_fkey'
  ) THEN
    ALTER TABLE alerts
      ADD CONSTRAINT alerts_source_id_fkey
      FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_signal_id_fkey'
  ) THEN
    ALTER TABLE alerts
      ADD CONSTRAINT alerts_signal_id_fkey
      FOREIGN KEY (signal_id) REFERENCES signals(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_data_job_id_fkey'
  ) THEN
    ALTER TABLE alerts
      ADD CONSTRAINT alerts_data_job_id_fkey
      FOREIGN KEY (data_job_id) REFERENCES data_jobs(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_alert_type_check'
  ) THEN
    ALTER TABLE alerts DROP CONSTRAINT alerts_alert_type_check;
  END IF;
  ALTER TABLE alerts
    ADD CONSTRAINT alerts_alert_type_check CHECK (
      alert_type = ANY (ARRAY[
        'date_conflict',
        'source_failure',
        'scheduler_failure',
        'data_stale',
        'spike_detected',
        'game_cancelled',
        'major_delay',
        'confidence_drop',
        'new_opportunity'
      ]::text[])
    );

  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_severity_check'
  ) THEN
    ALTER TABLE alerts DROP CONSTRAINT alerts_severity_check;
  END IF;
  ALTER TABLE alerts
    ADD CONSTRAINT alerts_severity_check CHECK (
      severity = ANY (ARRAY['info', 'warning', 'critical']::text[])
    );

  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'alerts_status_check'
  ) THEN
    ALTER TABLE alerts DROP CONSTRAINT alerts_status_check;
  END IF;
  ALTER TABLE alerts
    ADD CONSTRAINT alerts_status_check CHECK (
      status = ANY (ARRAY['open', 'acknowledged', 'resolved', 'dismissed']::text[])
    );
END $$;

CREATE INDEX IF NOT EXISTS alerts_status_type_created_at_idx
  ON alerts (status, alert_type, created_at DESC);
CREATE INDEX IF NOT EXISTS alerts_source_id_idx ON alerts (source_id);
CREATE INDEX IF NOT EXISTS alerts_data_job_id_idx ON alerts (data_job_id);
CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_source_failure_idx
  ON alerts (source_id)
  WHERE alert_type = 'source_failure' AND status = 'open' AND source_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS alerts_open_scheduler_failure_idx
  ON alerts ((conflict_detail->>'jobKey'))
  WHERE alert_type = 'scheduler_failure' AND status = 'open';
