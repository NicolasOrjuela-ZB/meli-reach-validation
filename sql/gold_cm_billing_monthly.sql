-- Facturación de CM contra lo medido: una fila por país × BU × mes, con la factura al lado.
-- Impresiones en exceso = CM cruzado − plataforma cruzada (impresiones que CM contó y la plataforma no).
-- usd_reclamable = exceso × CPM facturado ÷ 1000. Solo usa líneas comparables (cruzadas por tool),
-- por eso es una estimación conservadora. Las facturas se cargan con scripts/load_invoices.py.
CREATE OR REPLACE VIEW `mbw-spl-brazil.ds_meli_reach_validation.gold_cm_billing_monthly`
OPTIONS(description="Facturación de CM360 por país, BU y mes contra impresiones medidas. Ver sql/gold_cm_billing_monthly.sql")
AS
WITH g AS (
  SELECT country, brand, month,
    SUM(IF(side='CM', impressions, 0)) AS cm_total,
    SUM(IF(side='CM' AND match_type IN ('Misma tool y taxo','Misma tool, distinta taxo'), impressions, 0)) AS cm_cruzado,
    SUM(IF(side='PLATAFORMA', impressions, 0)) AS plataforma_total,
    SUM(IF(side='PLATAFORMA' AND match_type IN ('Misma tool y taxo','Misma tool, distinta taxo'), impressions, 0)) AS plataforma_cruzada
  FROM `mbw-spl-brazil.ds_meli_reach_validation.gold_impressions_monthly` GROUP BY 1,2,3)
SELECT g.country, g.brand, g.month,
  i.invoice_id, i.impressions_billed, i.cost_usd, i.cpm_usd,
  g.cm_total, g.cm_cruzado, g.plataforma_total, g.plataforma_cruzada,
  SAFE_DIVIDE(g.cm_cruzado, g.cm_total) AS cobertura_cm,
  SAFE_DIVIDE(g.plataforma_cruzada, g.plataforma_total) AS cobertura_plataforma,
  SAFE_DIVIDE(g.cm_total, i.impressions_billed) - 1 AS cm_vs_facturado,
  g.plataforma_cruzada - g.cm_cruzado AS var_impresiones,
  SAFE_DIVIDE(g.plataforma_cruzada, g.cm_cruzado) - 1 AS var_pct,
  g.cm_cruzado - g.plataforma_cruzada AS impresiones_exceso,
  (g.cm_cruzado - g.plataforma_cruzada) * i.cpm_usd / 1000 AS usd_reclamable,
  SAFE_DIVIDE((g.cm_cruzado - g.plataforma_cruzada) * i.cpm_usd / 1000, i.cost_usd) AS pct_factura_reclamable
FROM g LEFT JOIN `mbw-spl-brazil.ds_meli_reach_validation.bronze_cm_invoices` i USING (country, brand, month)
