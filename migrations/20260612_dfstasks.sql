CREATE TABLE IF NOT EXISTS dfstasks (
  task_id text PRIMARY KEY,
  source text NOT NULL DEFAULT 'unknown',
  status text NOT NULL DEFAULT 'submitted',
  error_msg text,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT chk_dfstasks_status
    CHECK (status = ANY (ARRAY['submitted', 'pending', 'processing', 'done', 'failed', 'done_with_error']::text[]))
);

CREATE INDEX IF NOT EXISTS idx_dfstasks_status_updated_at
  ON dfstasks (status, updated_at);

CREATE INDEX IF NOT EXISTS idx_dfstasks_source_status
  ON dfstasks (source, status);
