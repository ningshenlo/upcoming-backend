UPDATE games
SET data_completeness = data_completeness * 100,
    updated_at = now()
WHERE data_completeness IS NOT NULL
  AND data_completeness > 0
  AND data_completeness <= 1;

WITH first_release AS (
  SELECT DISTINCT ON (game_id)
    game_id,
    date AS release_date
  FROM release_events
  WHERE event_type = 'release'
    AND date IS NOT NULL
  ORDER BY game_id,
    CASE date_accuracy
      WHEN 'exact' THEN 5
      WHEN 'week' THEN 4
      WHEN 'month' THEN 3
      WHEN 'quarter' THEN 2
      WHEN 'year' THEN 1
      ELSE 0
    END DESC,
    date ASC,
    created_at ASC
)
UPDATE games
SET first_release_date = first_release.release_date,
    updated_at = now()
FROM first_release
WHERE games.id = first_release.game_id
  AND games.first_release_date IS NULL;
