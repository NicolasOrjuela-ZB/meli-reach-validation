-- Cruce CM ↔ plataforma a nivel línea. Una fila por línea de CM (y por línea de plataforma sin CM).
--
-- Decisiones:
--  · Tools repartidos en varios placements de CM (mismos creativos, varias líneas): se consolidan en una
--    sola fila (silver_cm_tool_groups). Impresiones se suman; el reach total sale de
--    bronze_cm_reach_tool_override o queda PENDING_TOOL_REACH.
--  · Una línea de CM que cruza con varias plataformas se queda con la que diga el maestro de network
--    codes (plataforma_compra). Si el maestro no la resuelve, igual queda una sola fila, marcada
--    multi_platform_unresolved para excluirla de las sumas.
--  · El join con `pl` usa la llave del grupo (platform, tool_id, gkey), no el STRING_AGG de campañas:
--    ese texto depende del orden de agregación y hacía que una fila apareciera o no entre ejecuciones.
CREATE OR REPLACE VIEW `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_vs_platform`
AS
WITH cm0 AS (SELECT * FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_reach_total` WHERE cm_reach_measurable),
grp AS (SELECT DISTINCT tool_id, rep_placement_id FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_cm_tool_groups`),
agg AS (
  SELECT g.tool_id, g.rep_placement_id, COUNT(*) n, SUM(c.impressions) impressions,
    MIN(c.first_impr_date) first_impr_date, MAX(c.last_impr_date) last_impr_date,
    MIN(c.eff_flight_start) eff_flight_start, MAX(c.eff_flight_end) eff_flight_end,
    LOGICAL_OR(c.is_running) is_running
  FROM cm0 c JOIN grp g ON g.tool_id = c.tool_id GROUP BY 1, 2),
cm AS (
  SELECT c.* FROM cm0 c LEFT JOIN grp g ON g.tool_id = c.tool_id WHERE g.tool_id IS NULL
  UNION ALL
  SELECT r.country, r.brand, r.advertiser, a.rep_placement_id, r.placement, a.tool_id, r.taxo_platform, r.taxo_type_campaign,
    r.taxonomy_valid, r.cm_reach_measurable,
    a.impressions, a.first_impr_date, a.last_impr_date, a.eff_flight_start, a.eff_flight_end,
    DATE_DIFF(a.eff_flight_end, a.eff_flight_start, DAY) + 1 AS eff_flight_days,
    a.is_running,
    o.reach_total_cm,
    CASE WHEN o.tool_id IS NULL THEN 'PENDING_TOOL_REACH' WHEN a.is_running THEN 'OK_PARTIAL_RUNNING' ELSE 'OK' END AS reach_status,
    CONCAT('TOOL_CONSOLIDATED_', CAST(a.n AS STRING)) AS reach_source_window, CAST(NULL AS DATE), CAST(NULL AS DATE),
    CAST(NULL AS INT64), CAST(NULL AS INT64), CAST(NULL AS INT64),
    SAFE_DIVIDE(a.impressions, o.reach_total_cm)
  FROM agg a
  JOIN cm0 r ON r.placement_id = a.rep_placement_id
  LEFT JOIN `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_reach_tool_override` o ON o.tool_id = a.tool_id),
pl_raw AS (SELECT * FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_platform_lines` WHERE country_code NOT IN ('MLB','MEC')),
pl AS (
  SELECT platform, tool_id, IF(tool_id IS NULL, UPPER(campaign), NULL) AS gkey,
    UPPER(ANY_VALUE(campaign)) AS name_key, COUNT(*) platform_campaigns_n,
    STRING_AGG(campaign, ' | ' ORDER BY impressions_platform DESC, campaign) platform_campaign,
    SUM(impressions_platform) impressions_platform, MAX(reach_total_platform) reach_total_platform,
    MIN(first_date) first_date, MAX(last_date) last_date, ANY_VALUE(country_code) country_code
  FROM pl_raw GROUP BY platform, tool_id, gkey),
by_tool AS (SELECT cm.placement_id, p.platform, p.tool_id AS p_tool, p.gkey AS p_gkey, 'TOOL_ID' match_type FROM cm JOIN pl p ON p.tool_id = cm.tool_id),
by_name AS (SELECT cm.placement_id, p.platform, p.tool_id AS p_tool, p.gkey AS p_gkey, 'NAME' match_type FROM cm JOIN pl p ON p.tool_id IS NULL AND p.name_key = UPPER(cm.placement)
            LEFT JOIN by_tool bt ON bt.placement_id = cm.placement_id WHERE bt.placement_id IS NULL),
m0 AS (SELECT * FROM by_tool UNION ALL SELECT * FROM by_name),
-- Plataforma de compra que declara el maestro para la taxonomía de cada línea de CM
cm_pc AS (
  SELECT c.placement_id, ANY_VALUE(t.plataforma_compra) AS pc
  FROM cm c LEFT JOIN `mbw-spl-brazil.ds_meli_reach_validation.silver_taxonomy_network_codes` t
    ON t.network_code = UPPER(TRIM(c.taxo_platform))
  GROUP BY 1),
m_rank AS (
  SELECT x.*,
    COUNT(DISTINCT x.platform) OVER (PARTITION BY x.placement_id) AS n_plat,
    SUM(x.pref) OVER (PARTITION BY x.placement_id) AS n_pref,
    ROW_NUMBER() OVER (PARTITION BY x.placement_id ORDER BY x.pref DESC, x.platform) AS rn
  FROM (SELECT m0.*, IF(UPPER(m0.platform) = IFNULL(cm_pc.pc, ''), 1, 0) AS pref
        FROM m0 LEFT JOIN cm_pc USING (placement_id)) x),
m AS (SELECT placement_id, platform, p_tool, p_gkey, match_type, n_plat > 1 AND n_pref <> 1 AS multi_unresolved
      FROM m_rank WHERE n_plat = 1 OR rn = 1),
cm_tool_n AS (SELECT tool_id, COUNT(*) cm_placements_per_tool FROM cm WHERE tool_id IS NOT NULL GROUP BY 1),
matched AS (
  SELECT cm.country, m.platform, m.match_type,
    cm.placement_id, cm.placement, cm.tool_id, cm.taxo_platform, cm.taxo_type_campaign,
    p.platform_campaign, p.platform_campaigns_n, t.cm_placements_per_tool,
    cm.impressions AS impressions_cm, p.impressions_platform, SAFE_DIVIDE(cm.impressions, p.impressions_platform) - 1 AS impressions_diff_pct,
    cm.reach_total_cm, cm.reach_status AS reach_status_cm, p.reach_total_platform, SAFE_DIVIDE(cm.reach_total_cm, p.reach_total_platform) - 1 AS reach_diff_pct,
    cm.first_impr_date AS cm_first_date, cm.last_impr_date AS cm_last_date, p.first_date AS platform_first_date, p.last_date AS platform_last_date,
    cm.is_running, m.multi_unresolved
  FROM cm JOIN m USING(placement_id)
  JOIN pl p ON p.platform = m.platform AND p.tool_id IS NOT DISTINCT FROM m.p_tool AND p.gkey IS NOT DISTINCT FROM m.p_gkey
  LEFT JOIN cm_tool_n t ON t.tool_id = cm.tool_id),
cm_only AS (
  SELECT cm.country, CAST(NULL AS STRING) platform, 'CM_ONLY' match_type, cm.placement_id, cm.placement, cm.tool_id, cm.taxo_platform, cm.taxo_type_campaign,
    CAST(NULL AS STRING) platform_campaign, CAST(NULL AS INT64) platform_campaigns_n, t.cm_placements_per_tool, cm.impressions impressions_cm, CAST(NULL AS FLOAT64) impressions_platform, CAST(NULL AS FLOAT64) impressions_diff_pct,
    cm.reach_total_cm, cm.reach_status reach_status_cm, CAST(NULL AS INT64) reach_total_platform, CAST(NULL AS FLOAT64) reach_diff_pct,
    cm.first_impr_date cm_first_date, cm.last_impr_date cm_last_date, CAST(NULL AS DATE) platform_first_date, CAST(NULL AS DATE) platform_last_date,
    cm.is_running, FALSE AS multi_unresolved
  FROM cm LEFT JOIN cm_tool_n t ON t.tool_id = cm.tool_id LEFT JOIN m ON m.placement_id = cm.placement_id WHERE m.placement_id IS NULL),
pl_only AS (
  SELECT p.country_code country, p.platform, 'PLATFORM_ONLY' match_type, CAST(NULL AS STRING) placement_id, CAST(NULL AS STRING) placement, p.tool_id, CAST(NULL AS STRING) taxo_platform, CAST(NULL AS STRING) taxo_type_campaign,
    p.platform_campaign, p.platform_campaigns_n, CAST(NULL AS INT64) cm_placements_per_tool, CAST(NULL AS INT64) impressions_cm, p.impressions_platform, CAST(NULL AS FLOAT64) impressions_diff_pct,
    CAST(NULL AS INT64) reach_total_cm, CAST(NULL AS STRING) reach_status_cm, p.reach_total_platform, CAST(NULL AS FLOAT64) reach_diff_pct,
    CAST(NULL AS DATE) cm_first_date, CAST(NULL AS DATE) cm_last_date, p.first_date platform_first_date, p.last_date platform_last_date,
    CAST(NULL AS BOOL) is_running, FALSE AS multi_unresolved
  FROM pl p LEFT JOIN (SELECT DISTINCT platform, p_tool, p_gkey FROM m) mm
    ON mm.platform = p.platform AND mm.p_tool IS NOT DISTINCT FROM p.tool_id AND mm.p_gkey IS NOT DISTINCT FROM p.gkey
  WHERE mm.platform IS NULL)
SELECT * FROM matched UNION ALL SELECT * FROM cm_only UNION ALL SELECT * FROM pl_only
