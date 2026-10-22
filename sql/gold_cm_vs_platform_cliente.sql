-- Vista que consume el dashboard (una fila por línea, nombres de columna como el archivo de cliente).
--
-- IIEE de CM: para tools repartidos en varios placements NO se suma el reach semanal entre placements
-- (serían usuarios repetidos). Esas líneas quedan con IIEE_CM NULL salvo que exista override semanal a
-- nivel tool en bronze_cm_reach_tool_weekly_override.
CREATE OR REPLACE VIEW `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_vs_platform_cliente`
AS
WITH freq AS (
  SELECT tool_id, ARRAY_AGG(chosen_frequency IGNORE NULLS ORDER BY week_start DESC LIMIT 1)[SAFE_OFFSET(0)] chosen_frequency
  FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_client_frequency_weekly` WHERE tool_id IS NOT NULL GROUP BY 1),
freq_wk AS (
  SELECT tool_id, week_start, ANY_VALUE(chosen_frequency) chosen_frequency
  FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_client_frequency_weekly` WHERE tool_id IS NOT NULL GROUP BY 1,2),
grp AS (SELECT DISTINCT tool_id, rep_placement_id FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_cm_tool_groups`),
-- IIEE del override semanal por tool, llevado a la frecuencia objetivo de cada semana
tool_iiee AS (
  SELECT o.tool_id, SUM(CASE f.chosen_frequency
      WHEN 1 THEN o.reach_1 WHEN 2 THEN o.reach_2 WHEN 3 THEN o.reach_3 WHEN 4 THEN o.reach_4 WHEN 5 THEN o.reach_5
      WHEN 6 THEN o.reach_6 WHEN 7 THEN o.reach_7 WHEN 8 THEN o.reach_8 WHEN 9 THEN o.reach_9 WHEN 10 THEN o.reach_10 END) AS iiee_cm
  FROM `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_reach_tool_weekly_override` o
  LEFT JOIN freq_wk f ON f.tool_id = o.tool_id AND f.week_start = o.week_start
  GROUP BY 1),
iiee_cm AS (
  SELECT i.placement_id, i.iiee_cm
  FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_iiee_total` i
  WHERE i.tool_id IS NULL OR i.tool_id NOT IN (SELECT tool_id FROM grp)
  UNION ALL
  SELECT g.rep_placement_id, t.iiee_cm FROM grp g JOIN tool_iiee t USING (tool_id)),
iiee_pl AS (SELECT platform, tool_id, SUM(IF(iiee_status='OK', reach_at_frequency_platform, 0)) iiee_platform
  FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_platform_iiee_weekly` WHERE tool_id IS NOT NULL GROUP BY 1,2),
cm AS (SELECT placement_id, advertiser FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_cm_lines`),
base AS (
  SELECT x.*, cm.advertiser, UPPER(TRIM(x.taxo_platform)) AS nc_raw
  FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_vs_platform` x LEFT JOIN cm USING(placement_id)
  WHERE x.match_type IN ('TOOL_ID','NAME','CM_ONLY'))
SELECT
  b.advertiser AS Advertiser, b.placement AS Placement, b.placement_id AS Placement_ID, b.tool_id AS TOOL, b.country AS Country,
  IF(f.chosen_frequency IS NULL, 'No Match base Meli', CONCAT('+', CAST(f.chosen_frequency AS STRING))) AS Chosen_Frequency,
  b.impressions_cm AS Impressions_CM,
  CAST(ROUND(b.impressions_platform) AS INT64) AS Impressions_Plataforma,
  SAFE_DIVIDE(b.impressions_platform, b.impressions_cm) - 1 AS VAR_Impressions,
  b.reach_total_cm AS Reach_CM,
  CAST(b.reach_total_platform AS INT64) AS Reach_Plataforma,
  SAFE_DIVIDE(b.reach_total_platform, b.reach_total_cm) - 1 AS VAR_Reach,
  i.iiee_cm AS IIEE_CM,
  p.iiee_platform AS IIEE_Plataforma,
  SAFE_DIVIDE(p.iiee_platform, i.iiee_cm) - 1 AS VAR_IIEE,
  COALESCE(b.platform_first_date, b.cm_first_date) AS Fecha_inicio,
  COALESCE(b.platform_last_date, b.cm_last_date) AS Fecha_fin,
  IF(b.platform_first_date IS NOT NULL, 'Plataforma', 'CM') AS Fecha_origen,
  b.cm_first_date AS Fecha_inicio_CM, b.cm_last_date AS Fecha_fin_CM,
  COALESCE(t.network_code_std, b.nc_raw) AS Network_Code,
  t.network_code IS NOT NULL AS Network_Code_Estandar,
  t.partner AS Partner, t.formato AS Formato, t.plataforma_compra AS Plataforma_Compra,
  SPLIT(b.placement, '_')[SAFE_OFFSET(8)] AS Campana,
  b.platform AS Plataforma,
  CASE WHEN b.multi_unresolved THEN TRUE
       WHEN b.platform = 'Pinterest' AND t.plataforma_compra IS NOT NULL AND t.plataforma_compra <> 'PINTEREST' THEN TRUE
       WHEN b.platform = 'TikTok' AND t.plataforma_compra IS NOT NULL AND t.plataforma_compra <> 'TIKTOK' THEN TRUE
       WHEN b.platform = 'DV360' AND t.plataforma_compra IN ('PINTEREST','TIKTOK','META','KWAI') THEN TRUE
       ELSE FALSE END AS Cruce_Dudoso,
  b.match_type AS Match, b.reach_status_cm AS Reach_Status_CM, b.is_running AS En_curso, b.platform_campaign AS Campana_Plataforma
FROM base b
LEFT JOIN freq f ON f.tool_id = b.tool_id
LEFT JOIN iiee_cm i USING(placement_id)
LEFT JOIN iiee_pl p ON p.tool_id = b.tool_id AND p.platform = b.platform
LEFT JOIN `mbw-spl-brazil.ds_meli_reach_validation.silver_taxonomy_network_codes` t ON t.network_code = b.nc_raw
