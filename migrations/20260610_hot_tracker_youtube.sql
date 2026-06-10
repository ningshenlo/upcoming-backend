CREATE TABLE IF NOT EXISTS youtube_tracked_videos (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  game_id text NOT NULL,
  video_id text,
  video_url text,
  channel_id text,
  channel_title text,
  title text,
  description text,
  thumbnail_url text,
  published_at timestamp with time zone,
  discovery_query text,
  discovery_status text NOT NULL DEFAULT 'not_found',
  match_confidence integer,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  last_discovered_at timestamp with time zone,
  last_checked_at timestamp with time zone,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT fk_youtube_tracked_videos_game
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
  CONSTRAINT uq_youtube_tracked_videos_game
    UNIQUE (game_id),
  CONSTRAINT chk_youtube_tracked_videos_discovery_status
    CHECK (discovery_status = ANY (ARRAY['found', 'not_found', 'error']::text[])),
  CONSTRAINT chk_youtube_tracked_videos_match_confidence
    CHECK (match_confidence IS NULL OR (match_confidence >= 0 AND match_confidence <= 100))
);

CREATE INDEX IF NOT EXISTS idx_youtube_tracked_videos_video_id
  ON youtube_tracked_videos (video_id)
  WHERE video_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_youtube_tracked_videos_discovery_status
  ON youtube_tracked_videos (discovery_status);
CREATE INDEX IF NOT EXISTS idx_youtube_tracked_videos_refresh
  ON youtube_tracked_videos (is_active, last_checked_at);
CREATE INDEX IF NOT EXISTS idx_youtube_tracked_videos_discovery
  ON youtube_tracked_videos (discovery_status, last_discovered_at);

CREATE TABLE IF NOT EXISTS youtube_video_daily_metrics (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  tracked_video_id text NOT NULL,
  game_id text NOT NULL,
  video_id text NOT NULL,
  captured_date date NOT NULL,
  view_count bigint,
  like_count bigint,
  comment_count bigint,
  data_job_id text,
  raw_statistics jsonb NOT NULL DEFAULT '{}'::jsonb,
  captured_at timestamp with time zone NOT NULL DEFAULT now(),
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  CONSTRAINT fk_youtube_video_daily_metrics_tracked_video
    FOREIGN KEY (tracked_video_id) REFERENCES youtube_tracked_videos(id) ON DELETE CASCADE,
  CONSTRAINT fk_youtube_video_daily_metrics_game
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
  CONSTRAINT fk_youtube_video_daily_metrics_data_job
    FOREIGN KEY (data_job_id) REFERENCES data_jobs(id) ON DELETE SET NULL,
  CONSTRAINT uq_youtube_video_daily_metrics_tracked_date
    UNIQUE (tracked_video_id, captured_date)
);

CREATE INDEX IF NOT EXISTS idx_youtube_video_daily_metrics_game_date
  ON youtube_video_daily_metrics (game_id, captured_date DESC);
CREATE INDEX IF NOT EXISTS idx_youtube_video_daily_metrics_video_date
  ON youtube_video_daily_metrics (video_id, captured_date DESC);
CREATE INDEX IF NOT EXISTS idx_youtube_video_daily_metrics_data_job_id
  ON youtube_video_daily_metrics (data_job_id);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'signals_signal_type_check'
  ) THEN
    ALTER TABLE signals DROP CONSTRAINT signals_signal_type_check;
  END IF;

  ALTER TABLE signals
    ADD CONSTRAINT signals_signal_type_check CHECK (
      signal_type = ANY (ARRAY[
        'google_trends',
        'reddit_activity',
        'reddit_question_density',
        'youtube_views',
        'youtube_likes',
        'youtube_comments',
        'serp_rank',
        'serp_reddit_presence',
        'store_wishlist',
        'store_rank',
        'keyword_volume',
        'social_mentions',
        'community_question_density'
      ]::text[])
    );
END $$;

CREATE INDEX IF NOT EXISTS idx_signals_game_type_observed
  ON signals (game_id, signal_type, observed_at DESC);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'data_jobs_job_type_check'
  ) THEN
    ALTER TABLE data_jobs DROP CONSTRAINT data_jobs_job_type_check;
  END IF;

  ALTER TABLE data_jobs
    ADD CONSTRAINT data_jobs_job_type_check CHECK (
      job_type = ANY (ARRAY[
        'official_release_sync',
        'igdb_discovery_sync',
        'steam_sync',
        'nintendo_sync',
        'playstation_sync',
        'xbox_sync',
        'youtube_track',
        'reddit_scan',
        'trends_fetch',
        'signal_calculate',
        'opportunity_score',
        'sitemap_generate',
        'page_quality_check'
      ]::text[])
    );
END $$;

INSERT INTO sources (
  name, slug, source_type, url, trust_level, refresh_frequency, parser_type, parser_config
)
VALUES (
  'YouTube Data API',
  'youtube',
  'youtube',
  'https://www.googleapis.com/youtube/v3',
  4,
  'daily',
  'youtube-data-api-v3',
  '{}'::jsonb
)
ON CONFLICT (slug) DO UPDATE SET
  name = EXCLUDED.name,
  source_type = EXCLUDED.source_type,
  url = EXCLUDED.url,
  trust_level = EXCLUDED.trust_level,
  refresh_frequency = EXCLUDED.refresh_frequency,
  parser_type = EXCLUDED.parser_type,
  parser_config = COALESCE(sources.parser_config, '{}'::jsonb) || EXCLUDED.parser_config,
  is_active = true,
  updated_at = now();
