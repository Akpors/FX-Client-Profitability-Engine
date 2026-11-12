{#-
  The trade fact table: int_trades_enriched, unchanged, as a table in the marts schema so the
  dashboard and notebooks read one schema. Formulas: docs/methodology/metrics.md section 1.
-#}
select * from {{ ref('int_trades_enriched') }}
