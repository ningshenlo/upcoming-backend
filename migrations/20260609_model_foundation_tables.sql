CREATE TABLE IF NOT EXISTS franchises (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  slug text NOT NULL UNIQUE,
  name text NOT NULL,
  description text,
  logo_url text,
  official_url text,
  igdb_franchise_id integer UNIQUE,
  search_volume integer,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS keywords (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  game_id text REFERENCES games(id) ON DELETE CASCADE,
  keyword text NOT NULL,
  normalized_keyword text NOT NULL,
  intent text NOT NULL CHECK (
    intent = ANY (ARRAY[
      'release_date',
      'launch_time',
      'platforms',
      'demo',
      'beta',
      'preload',
      'system_requirements',
      'game_pass',
      'guide',
      'map',
      'calculator',
      'tracker',
      'tier_list',
      'codes',
      'alternative',
      'review',
      'reddit_discussion'
    ]::text[])
  ),
  volume integer,
  cpc numeric,
  difficulty integer CHECK (difficulty IS NULL OR (difficulty >= 0 AND difficulty <= 100)),
  serp_top_urls text[] NOT NULL DEFAULT '{}'::text[],
  reddit_ranked_url text,
  serp_has_reddit boolean,
  our_page_rank integer,
  priority_score integer CHECK (priority_score IS NULL OR (priority_score >= 0 AND priority_score <= 100)),
  source_id text REFERENCES sources(id) ON DELETE SET NULL,
  first_seen_at timestamp with time zone NOT NULL,
  last_seen_at timestamp with time zone NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS keywords_normalized_game_idx
  ON keywords (normalized_keyword, COALESCE(game_id, ''));
CREATE INDEX IF NOT EXISTS keywords_game_id_idx ON keywords (game_id);
CREATE INDEX IF NOT EXISTS keywords_intent_idx ON keywords (intent);
CREATE INDEX IF NOT EXISTS keywords_priority_score_idx ON keywords (priority_score DESC);

CREATE TABLE IF NOT EXISTS opportunities (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  game_id text REFERENCES games(id) ON DELETE CASCADE,
  keyword_id text REFERENCES keywords(id) ON DELETE SET NULL,
  opportunity_type text NOT NULL CHECK (
    opportunity_type = ANY (ARRAY[
      'seo_page',
      'micro_tool',
      'reddit_launch',
      'affiliate_page',
      'content_brief',
      'data_gap',
      'community_probe'
    ]::text[])
  ),
  title text NOT NULL,
  hypothesis text NOT NULL,
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  score integer NOT NULL CHECK (score >= 0 AND score <= 100),
  effort_score integer NOT NULL CHECK (effort_score >= 0 AND effort_score <= 100),
  impact_score integer NOT NULL CHECK (impact_score >= 0 AND impact_score <= 100),
  effort text NOT NULL CHECK (effort = ANY (ARRAY['low', 'medium', 'high']::text[])),
  risk_level text NOT NULL CHECK (risk_level = ANY (ARRAY['low', 'medium', 'high']::text[])),
  status text NOT NULL DEFAULT 'new' CHECK (
    status = ANY (ARRAY['new', 'reviewing', 'planned', 'in_progress', 'launched', 'dismissed']::text[])
  ),
  dismiss_reason text,
  next_action text,
  assigned_to text,
  result jsonb,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS opportunities_game_id_idx ON opportunities (game_id);
CREATE INDEX IF NOT EXISTS opportunities_keyword_id_idx ON opportunities (keyword_id);
CREATE INDEX IF NOT EXISTS opportunities_status_score_idx ON opportunities (status, score DESC);

CREATE TABLE IF NOT EXISTS communities (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  platform text NOT NULL CHECK (
    platform = ANY (ARRAY['reddit', 'discord', 'youtube', 'steam_forum', 'x', 'tiktok']::text[])
  ),
  name text NOT NULL,
  url text NOT NULL,
  related_game_ids text[] NOT NULL DEFAULT '{}'::text[],
  related_franchise_ids text[] NOT NULL DEFAULT '{}'::text[],
  member_count integer,
  activity_level text CHECK (activity_level IS NULL OR activity_level = ANY (ARRAY['low', 'medium', 'high']::text[])),
  rules_summary text,
  promotion_policy text NOT NULL DEFAULT 'unknown' CHECK (
    promotion_policy = ANY (ARRAY['strict', 'moderate', 'friendly', 'unknown']::text[])
  ),
  question_density text CHECK (question_density IS NULL OR question_density = ANY (ARRAY['low', 'medium', 'high']::text[])),
  last_checked_at timestamp with time zone,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS communities_platform_url_idx ON communities (platform, url);
CREATE INDEX IF NOT EXISTS communities_platform_idx ON communities (platform);

CREATE TABLE IF NOT EXISTS search_trends (
  id text PRIMARY KEY DEFAULT (gen_random_uuid())::text,
  game_id text REFERENCES games(id) ON DELETE CASCADE,
  keyword text NOT NULL,
  data_points jsonb NOT NULL DEFAULT '[]'::jsonb,
  geo text,
  time_window text NOT NULL CHECK (time_window = ANY (ARRAY['7d', '30d', '90d', '12m']::text[])),
  peak_value integer,
  peak_date date,
  average_value real,
  trend text NOT NULL CHECK (trend = ANY (ARRAY['rising', 'stable', 'falling', 'spike']::text[])),
  fetched_at timestamp with time zone NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS search_trends_game_id_idx ON search_trends (game_id);
CREATE INDEX IF NOT EXISTS search_trends_keyword_idx ON search_trends (keyword);
CREATE INDEX IF NOT EXISTS search_trends_fetched_at_idx ON search_trends (fetched_at DESC);

ALTER TABLE store_links ADD COLUMN IF NOT EXISTS affiliate_url text;
ALTER TABLE pages ADD COLUMN IF NOT EXISTS company_id text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'games_franchise_id_fkey'
  ) THEN
    ALTER TABLE games
      ADD CONSTRAINT games_franchise_id_fkey
      FOREIGN KEY (franchise_id) REFERENCES franchises(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'pages_franchise_id_fkey'
  ) THEN
    ALTER TABLE pages
      ADD CONSTRAINT pages_franchise_id_fkey
      FOREIGN KEY (franchise_id) REFERENCES franchises(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'pages_company_id_fkey'
  ) THEN
    ALTER TABLE pages
      ADD CONSTRAINT pages_company_id_fkey
      FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE SET NULL
      NOT VALID;
  END IF;
END $$;

ALTER TABLE games VALIDATE CONSTRAINT games_franchise_id_fkey;
ALTER TABLE pages VALIDATE CONSTRAINT pages_franchise_id_fkey;
ALTER TABLE pages VALIDATE CONSTRAINT pages_company_id_fkey;

CREATE INDEX IF NOT EXISTS games_franchise_id_idx ON games (franchise_id);
CREATE INDEX IF NOT EXISTS pages_company_id_idx ON pages (company_id);
CREATE INDEX IF NOT EXISTS pages_franchise_id_idx ON pages (franchise_id);
