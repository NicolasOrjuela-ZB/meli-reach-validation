# Validación de Reach · CM360 vs Plataformas

Dashboard y pipeline para comparar impresiones, reach total e IIEE de **Campaign Manager 360** contra la data directa de cada plataforma (DV360, Pinterest, TikTok) para MercadoLibre LATAM.

- **Dashboard:** `docs/index.html` (un solo archivo, sin dependencias; se publica en GitHub Pages y se carga en el Hub ZetaBé).
- **Datos:** `mbw-spl-brazil.ds_meli_reach_validation` en BigQuery (estructura medallion: `bronze_*` → `silver_*` → `gold_*`).
- **Tabla para equipos/cliente:** `gold_cm_vs_platform_cliente` (misma estructura que la hoja "Pinterest vs CM"; el dashboard la descarga como CSV).

## Cómo se actualiza

| Qué | Cómo | Cuándo |
|---|---|---|
| Dashboard | GitHub Action `build.yml` regenera `docs/index.html` desde las vistas gold | Lunes 07:15 (Bogotá) o a mano desde Actions |
| CM semanal con pivot | Exportar de CM (Reach report, dimensión Week, 1+…10+, CSV) y cargar con `scripts/load_cm.py` | Semanal. Se reemplazan solo las semanas que trae el archivo |
| CM reach por ventana | Exportar la ventana (rango ≤ 90 días, filtro de placements) y cargar con `load_cm.py` | Solo líneas **nuevas** o **en curso**. Las cerradas no se vuelven a pedir |
| DV360 semanal | Export DV_REACH_PIVOTED (semana dom–sáb, por Insertion Order) y cargar con `load_cm.py` | Semanal |
| Pinterest semanal | Archivo "Day to Day Cumulative & Weekly Trend" del cliente y cargar con `load_cm.py` | Cuando lo entregue el cliente |
| Base cliente (chosen frequency) y Adverity | Ya están en BigQuery | Automático |

```bash
# cargar uno o varios archivos (detecta el tipo solo)
python scripts/load_cm.py carpeta_con_csv/ --dry-run   # solo valida
python scripts/load_cm.py carpeta_con_csv/             # carga
python scripts/build_dashboard.py                      # regenera docs/index.html
```

Requiere `pip install google-cloud-bigquery pandas db-dtypes` y credencial de GCP (`gcloud auth application-default login` o `GOOGLE_APPLICATION_CREDENTIALS`). En Actions la credencial va en el secret `GCP_CREDENTIALS` (JSON de service account con `BigQuery Data Viewer` + `Job User` sobre el proyecto).

## Reglas de negocio (no negociables)

1. **El reach nunca se suma** entre líneas ni entre períodos. Los totales de reach del dashboard son sumas de líneas y sirven solo para comparar CM contra plataforma, no como reach único.
2. **CM deduplica reach solo dentro de rangos ≤ 90 días.** El reach total de una línea sale de una ventana que cubre su *flight efectivo* (el período con el 99% de sus impresiones; se recorta el goteo). Líneas con flight efectivo > 90 días no tienen reach total en CM (`LONG_FLIGHT`), solo límites de validación.
3. **IIEE** = suma del reach semanal (dom–sáb) a la **chosen frequency** del cliente (`BT_BRD_SHARE_CCEE_DAILY`, por tool y semana). Se calcula igual en CM y en plataforma. La base cliente solo aporta la frecuencia.
4. **Cruce CM ↔ plataforma por `tool_id`** (código de 8 dígitos en la taxonomía); fallback por nombre exacto.
5. **Meta queda fuera:** CM no mide reach ahí (reach/impresiones ≈ 0.01%). MLB y MEC no están en CM.
6. **Refresco:** una línea se congela cuando su flight efectivo terminó hace más de 7 días (CM cierra números a los 7 días). Solo se vuelven a pedir ventanas de líneas nuevas o en curso. El semanal reemplaza solo las últimas semanas.
7. `bronze_cm_impressions_daily` es **histórico** (carga única, ene-2025 → oct-2026). En adelante impresiones y flight salen del semanal.

## Limitaciones de CM encontradas

- Placement Start/End Date no reflejan cuándo sirvió la línea (2,801 líneas sirvieron después de su End Date): se usan fechas de actividad real.
- Reach modelado de **Pinterest** en CM solo existe para los últimos ~15 meses y solo en reportes con rango ≤ 1 año; antes de jul-2025 no hay. Y CM mide ~1/4 del reach que reporta Pinterest.
- **DV360 video y display:** CM cuenta 29–84% más impresiones que DV360 con el mismo reach (YouTube cuadra). Pendiente de revisar con ad ops.
- Reach de CM para Meta/IG/FB no es utilizable.

## Estados de `reach_status` (gold_cm_reach_total)

`OK` flight cerrado · `OK_PARTIAL_RUNNING` activa, reach crecerá · `LONG_FLIGHT` sin reach total en CM · `STARTED_BEFORE_RANGE` activa el 1-ene-2025 · `NO_REACH_CM` CM devolvió "-" · `NOT_MEASURABLE_CM` Meta · `REVIEW_*` revisar · `PENDING_WINDOW` ventana no cargada.

## Estructura del dataset

```
bronze_cm_impressions_daily      placement × día (histórico)
bronze_cm_placements             catálogo
bronze_cm_reach_quarter          placement × trimestre, pivot (límites de validación)
bronze_cm_reach_window           placement × ventana R01..R30, reach 1+
bronze_cm_window_plan            placement → ventana, flight efectivo, estado
bronze_cm_reach_weekly           placement × semana, pivot 1+..10+
bronze_platform_reach_weekly     plataforma × línea × semana, pivot
silver_cm_lines                  línea con taxonomía, tool_id, flight, estado
silver_cm_reach_bounds           piso/techo de validación
silver_cm_reach_window_valid     reach de ventana validado
silver_cm_reach_weekly           semanal CM con contexto
silver_client_frequency_weekly   chosen frequency por tool × semana (base cliente)
silver_platform_lines            impresiones y reach total directo de plataforma (Adverity 2025 + gold merge 2026)
silver_platform_reach_weekly     semanal plataforma con tool_id
gold_cm_reach_total              consolidada CM por línea
gold_cm_iiee_weekly / _total     IIEE desde CM
gold_platform_iiee_weekly        IIEE desde plataforma
gold_cm_vs_platform              cruce por línea (impresiones, reach)
gold_cm_vs_platform_weekly       cruce semanal
gold_cm_vs_platform_cliente      tabla descargable, estructura cliente
```

Las definiciones de las vistas están en `sql/views.sql`.

## Pendientes

- TikTok: cargar su archivo semanal con pivot cuando el cliente lo entregue (`load_cm.py` necesita un parser nuevo si el formato difiere de DV360).
- Automatizar la extracción de CM (BigQuery Export programado para el semanal; API para ventanas) y DV360 (reporte programado a GCS).
- Migrar la credencial a service account (compartida con Learning Agenda).
