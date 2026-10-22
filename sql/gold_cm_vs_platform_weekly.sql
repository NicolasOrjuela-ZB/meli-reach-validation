-- Serie semanal CM vs plataforma por línea.
-- Los tools repartidos en varios placements van como una sola serie: las impresiones se suman, pero el
-- reach y el IIEE de CM NO (serían usuarios repetidos entre placements): salen del override semanal a
-- nivel tool (bronze_cm_reach_tool_weekly_override) o quedan NULL.
CREATE OR REPLACE VIEW `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_vs_platform_weekly`
AS
WITH cm0 AS (
  SELECT country, brand, placement_id, placement, tool_id, taxo_platform, week_start, week_end, is_partial_week, chosen_frequency,
    impressions AS impressions_cm, reach_1 AS reach_1_cm, reach_at_frequency AS iiee_cm, iiee_status AS iiee_status_cm
  FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_iiee_weekly`
  WHERE tool_id IS NOT NULL AND iiee_status <> 'NOT_MEASURABLE_CM'),
grp AS (SELECT DISTINCT tool_id, rep_placement_id FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_cm_tool_groups`),
freq_wk AS (
  SELECT tool_id, week_start, ANY_VALUE(chosen_frequency) chosen_frequency
  FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_client_frequency_weekly` WHERE tool_id IS NOT NULL GROUP BY 1,2),
-- Override semanal por tool, ya resuelto a la frecuencia objetivo de cada semana
tw AS (
  SELECT o.tool_id, o.week_start, MAX(o.reach_1) AS reach_1,
    MAX(CASE f.chosen_frequency
      WHEN 1 THEN o.reach_1 WHEN 2 THEN o.reach_2 WHEN 3 THEN o.reach_3 WHEN 4 THEN o.reach_4 WHEN 5 THEN o.reach_5
      WHEN 6 THEN o.reach_6 WHEN 7 THEN o.reach_7 WHEN 8 THEN o.reach_8 WHEN 9 THEN o.reach_9 WHEN 10 THEN o.reach_10 END) AS iiee
  FROM `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_reach_tool_weekly_override` o
  LEFT JOIN freq_wk f ON f.tool_id = o.tool_id AND f.week_start = o.week_start
  GROUP BY 1,2),
cm_grp AS (
  SELECT ANY_VALUE(c.country) country, ANY_VALUE(c.brand) brand, g.rep_placement_id AS placement_id,
    ANY_VALUE(c.placement) placement, c.tool_id, ANY_VALUE(c.taxo_platform) taxo_platform,
    c.week_start, ANY_VALUE(c.week_end) week_end, LOGICAL_OR(c.is_partial_week) is_partial_week,
    ANY_VALUE(c.chosen_frequency) chosen_frequency,
    SUM(c.impressions_cm) impressions_cm
  FROM cm0 c JOIN grp g ON g.tool_id = c.tool_id
  GROUP BY c.tool_id, g.rep_placement_id, c.week_start),
cm AS (
  SELECT * FROM cm0 WHERE tool_id NOT IN (SELECT tool_id FROM grp)
  UNION ALL
  SELECT k.country, k.brand, k.placement_id, k.placement, k.tool_id, k.taxo_platform, k.week_start, k.week_end,
    k.is_partial_week, k.chosen_frequency, k.impressions_cm,
    tw.reach_1 AS reach_1_cm, tw.iiee AS iiee_cm,
    IF(tw.iiee IS NULL, 'NO_REACH_CM', 'OK') AS iiee_status_cm
  FROM cm_grp k LEFT JOIN tw ON tw.tool_id = k.tool_id AND tw.week_start = k.week_start),
pl AS (
  SELECT platform, tool_id, week_start, impressions_platform, reach_1_platform, reach_at_frequency_platform AS iiee_platform, iiee_status AS iiee_status_platform
  FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_platform_iiee_weekly` WHERE tool_id IS NOT NULL)
SELECT cm.country, cm.brand, pl.platform, cm.placement_id, cm.placement, cm.tool_id, cm.taxo_platform,
  cm.week_start, cm.week_end, cm.is_partial_week, cm.chosen_frequency,
  cm.impressions_cm, pl.impressions_platform, SAFE_DIVIDE(pl.impressions_platform, cm.impressions_cm) - 1 AS var_impressions,
  cm.reach_1_cm, pl.reach_1_platform, SAFE_DIVIDE(pl.reach_1_platform, cm.reach_1_cm) - 1 AS var_reach,
  cm.iiee_cm, pl.iiee_platform, SAFE_DIVIDE(pl.iiee_platform, cm.iiee_cm) - 1 AS var_iiee,
  cm.iiee_status_cm, pl.iiee_status_platform,
  CASE WHEN pl.tool_id IS NULL THEN 'CM_ONLY' ELSE 'MATCH' END AS match_type
FROM cm LEFT JOIN pl ON pl.tool_id = cm.tool_id AND pl.week_start = cm.week_start
