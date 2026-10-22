-- Impresiones mensuales CM vs plataforma, una fila por lado (CM / PLATAFORMA) x línea x mes.
-- match_type se define a nivel de línea (toda su vida), dentro de la misma familia de plataforma:
--   Misma tool y taxo         · la tool existe en el otro lado y hay una línea con el mismo nombre exacto
--   Misma tool, distinta taxo · la tool existe en el otro lado, pero ningún nombre coincide
--   Misma taxo, sin tool      · nombre idéntico pero sin código de tool de 8 dígitos
--   Solo CM / Solo plataforma · la tool no aparece en el otro lado
--   Sin fuente de plataforma  · plataformas sin data directa (Meta, Medios directos, Google Ads, Twitter…): solo lado CM
-- "Cruce por tool" = Misma tool y taxo + Misma tool, distinta taxo.
-- platform_group: desde la taxonomía (campo 3) estandarizada con el maestro de network codes. DV360 se abre en
--   Open / YouTube / Deals (G-DV-DL-*, medios vía DV) / DOOH / Otros. Las líneas de plataforma que cruzan por tool
--   toman el grupo de su línea de CM (la taxonomía de CM manda).
-- CM: bronze_cm_impressions_daily. Plataforma: Adverity 2025 + bronze_platform_daily_2026, cortado a la última fecha de CM.
CREATE OR REPLACE VIEW `mbw-spl-brazil.ds_meli_reach_validation.gold_impressions_monthly`
OPTIONS(description="Impresiones mensuales CM vs plataforma por línea y tipo de cruce (tool / tool+taxo / tool distinta taxo). Ver sql/gold_impressions_monthly.sql")
AS
WITH cutoff AS (SELECT MAX(date) d FROM `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_impressions_daily`),
cm_countries AS (SELECT DISTINCT SUBSTR(advertiser,1,3) c FROM `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_placements`),
nc AS (
  SELECT network_code, ANY_VALUE(network_code_std) std, ANY_VALUE(plataforma_compra) plat, ANY_VALUE(implementation) impl,
         ANY_VALUE(partner) partner, ANY_VALUE(formato) formato
  FROM `mbw-spl-brazil.ds_meli_reach_validation.silver_taxonomy_network_codes` GROUP BY 1),
cm AS (
  SELECT 'CM' AS side, d.placement_id AS line_id, TRIM(p.placement) AS line_name, SUBSTR(p.advertiser,1,3) AS country,
         IF(p.advertiser LIKE '%MercadoPago%','MP','ML') AS brand, CAST(NULL AS STRING) AS source_platform,
         DATE_TRUNC(d.date, MONTH) AS month, SUM(d.impressions) AS impressions
  FROM `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_impressions_daily` d
  JOIN `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_placements` p USING (placement_id)
  WHERE d.date >= '2025-01-01'
  GROUP BY 1,2,3,4,5,6,7),
pl_src AS (
  SELECT Platform AS platform, Campaign_Ajustado AS campaign, Date AS date, Impressions AS impressions
  FROM `mbw-spl-brazil.meli_latam_adverity.TABLE_DLY_DIGITAL_UNIFIED_2025`
  WHERE Platform IN ('DV360','TikTok','Pinterest') AND Date >= '2025-01-01' AND Date < '2026-01-01'
  UNION ALL
  SELECT Platform, Campaign, Date, Impressions
  FROM `mbw-spl-brazil.ds_meli_reach_validation.bronze_platform_daily_2026`
  WHERE Platform IN ('DV360','TikTok','Pinterest') AND Date >= '2026-01-01'),
pl AS (
  SELECT 'PLATAFORMA', CAST(NULL AS STRING), TRIM(campaign), SUBSTR(TRIM(campaign),1,3),
         IF(SPLIT(TRIM(campaign),'_')[SAFE_OFFSET(1)] = 'MP','MP','ML'), platform,
         DATE_TRUNC(date, MONTH), CAST(ROUND(SUM(impressions)) AS INT64)
  FROM pl_src, cutoff
  WHERE date <= cutoff.d AND SUBSTR(TRIM(campaign),1,3) IN (SELECT c FROM cm_countries)
  GROUP BY 1,2,3,4,5,6,7
  HAVING SUM(impressions) > 0),
lines AS (SELECT * FROM cm UNION ALL SELECT * FROM pl),
enr AS (
  SELECT l.*,
    UPPER(SPLIT(l.line_name,'_')[SAFE_OFFSET(2)]) AS code3,
    REGEXP_EXTRACT(l.line_name, r'_(\d{8})(?:[^0-9]|$)') AS tool_id,
    UPPER(REGEXP_REPLACE(l.line_name, r'\s+', '')) AS name_key,
    SPLIT(l.line_name,'_')[SAFE_OFFSET(8)] AS campana
  FROM lines l),
-- Códigos viejos con prefijo MEDIOS-/DDL- (medios directos): se busca también sin el prefijo.
enr2 AS (SELECT e.*, REGEXP_REPLACE(IFNULL(e.code3,''), r'^(MEDIOS|DDL)-', '') AS code_s FROM enr e),
grp AS (
  SELECT e.*, COALESCE(n1.std, n2.std) AS network_code_std, COALESCE(n1.plat, n2.plat) AS plat,
    COALESCE(n1.partner, n2.partner) AS partner, COALESCE(n1.formato, n2.formato) AS formato,
    UPPER(IFNULL(COALESCE(n1.impl, n2.impl),'')) AS impl_u,
    COALESCE(n1.std, n2.std, e.code_s) AS code_eff
  FROM enr2 e LEFT JOIN nc n1 ON n1.network_code = e.code3 LEFT JOIN nc n2 ON n2.network_code = e.code_s),
cls AS (
  SELECT g.*,
    CASE
      WHEN side='PLATAFORMA' AND source_platform='TikTok' THEN 'TikTok'
      WHEN side='PLATAFORMA' AND source_platform='Pinterest' THEN 'Pinterest'
      WHEN (side='PLATAFORMA' AND source_platform='DV360') OR plat='DV360'
           OR (plat IS NULL AND REGEXP_CONTAINS(code_s, r'^(G-DV|DV-DV|DV|G-G-DV)-')) THEN
        CASE
          WHEN REGEXP_CONTAINS(IFNULL(code3,''), r'^DDL(-|$)') THEN 'DV360 · Deals'
          WHEN REGEXP_CONTAINS(code_eff, r'-YT(-|$)|BUMPER|MASTHEAD') OR REGEXP_CONTAINS(impl_u, r'YOUTUBE') THEN 'DV360 · YouTube'
          WHEN REGEXP_CONTAINS(code_eff, r'DOOH') THEN 'DV360 · DOOH'
          WHEN STARTS_WITH(impl_u,'DV/OPEN') OR impl_u LIKE 'TOP PERFORMER%'
               OR REGEXP_CONTAINS(code_eff, r'^(G-DV|DV-DV)-(DISPLAY|VIDEO|AUDIO|VIDISP|VID-TP|DISP-TP|DL-NAT)$') THEN 'DV360 · Open'
          WHEN REGEXP_CONTAINS(code_eff, r'^G-DV-(DL|DEAL)-') THEN 'DV360 · Deals'
          ELSE 'DV360 · Otros' END
      -- DDL-* son deals comprados vía DV360: van a Deals aunque el maestro los marque DIRECTO
      WHEN REGEXP_CONTAINS(IFNULL(code3,''), r'^DDL(-|$)') THEN 'DV360 · Deals'
      WHEN plat='PINTEREST' OR REGEXP_CONTAINS(IFNULL(code3,''), r'^(PINTEREST|PINT)-') THEN 'Pinterest'
      WHEN plat='TIKTOK' OR REGEXP_CONTAINS(IFNULL(code3,''), r'^(TT|TTK)-') THEN 'TikTok'
      WHEN plat='META' OR REGEXP_CONTAINS(IFNULL(code3,''), r'^(META|IG|FB)-') THEN 'Meta'
      WHEN plat='TWITTER' OR REGEXP_CONTAINS(code_s, r'^(TW|TWT|X)(-|$)') THEN 'Twitter'
      WHEN plat='DIRECTO' OR REGEXP_CONTAINS(IFNULL(code3,''), r'^(DD|MEDIOS)-') THEN 'Medios directos'
      WHEN plat IS NOT NULL THEN INITCAP(plat)
      ELSE 'Sin clasificar' END AS platform_group
  FROM grp g),
fam AS (SELECT c.*, IF(STARTS_WITH(platform_group,'DV360'),'DV360',platform_group) AS family FROM cls c),
k AS (SELECT side, family, tool_id, name_key FROM fam GROUP BY 1,2,3,4),
tg AS (
  SELECT family, tool_id, ARRAY_AGG(platform_group ORDER BY impressions DESC LIMIT 1)[OFFSET(0)] AS cm_group
  FROM fam WHERE side = 'CM' AND tool_id IS NOT NULL GROUP BY 1,2),
m0 AS (
  SELECT f.*,
    EXISTS(SELECT 1 FROM k WHERE k.side<>f.side AND k.family=f.family AND k.tool_id=f.tool_id) AS tool_other,
    EXISTS(SELECT 1 FROM k WHERE k.side<>f.side AND k.family=f.family AND k.name_key=f.name_key) AS name_other
  FROM fam f),
mm AS (
  SELECT m0.* REPLACE (IF(m0.side='PLATAFORMA' AND m0.tool_other, COALESCE(tg.cm_group, m0.platform_group), m0.platform_group) AS platform_group)
  FROM m0 LEFT JOIN tg ON tg.family = m0.family AND tg.tool_id = m0.tool_id)
SELECT side, month, country, brand, platform_group, family, source_platform,
  CASE
    WHEN side='CM' AND family NOT IN ('DV360','TikTok','Pinterest') THEN 'Sin fuente de plataforma'
    WHEN tool_id IS NOT NULL AND tool_other AND name_other THEN 'Misma tool y taxo'
    WHEN tool_id IS NOT NULL AND tool_other THEN 'Misma tool, distinta taxo'
    WHEN name_other THEN 'Misma taxo, sin tool'
    WHEN side='CM' THEN 'Solo CM'
    ELSE 'Solo plataforma' END AS match_type,
  tool_id, line_id, line_name, code3 AS network_code_raw, COALESCE(network_code_std, code3) AS network_code,
  partner, formato, campana, impressions
FROM mm
