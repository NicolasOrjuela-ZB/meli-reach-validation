# SQL

`views.sql` contiene el DDL vigente de todas las tablas y vistas de `mbw-spl-brazil.ds_meli_reach_validation`.
Se genera con `python scripts/export_views.py` (requiere credencial de BQ). Correrlo después de cambiar cualquier vista
y subir el cambio al repo, para que la definición quede versionada.

Dependencias (de abajo hacia arriba):

```
bronze_* (tablas)
  └─ silver_cm_lines, silver_cm_reach_bounds, silver_client_frequency_weekly, silver_platform_lines, silver_platform_reach_weekly
       └─ silver_cm_reach_window_valid, silver_cm_reach_weekly
            └─ gold_cm_reach_total, gold_cm_iiee_weekly, gold_platform_iiee_weekly
                 └─ gold_cm_iiee_total, gold_cm_vs_platform, gold_cm_vs_platform_weekly
                      └─ gold_cm_vs_platform_cliente
```
