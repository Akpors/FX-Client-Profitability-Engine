{#-
  Compare numeric columns in unit tests at 4 decimal places.

  dbt compares the model's output with the expected rows exactly. The model keeps full numeric
  precision (909.09090909090909...), while a test is easier to read and to check by hand when it
  says 909.0909. This overrides dbt's own `get_unit_test_sql` (enabled by the `dispatch` block in
  dbt_project.yml) so that numeric and floating-point columns, and only those, are rounded to
  4 dp on both the actual and the expected side. The model's stored values are not rounded;
  nothing outside `dbt test` / `dbt build` unit tests is affected.

  Everything else in this macro is dbt's default `default__get_unit_test_sql`, unchanged.
-#}
{% macro unit_test_compare_expression(column_name, column_types) -%}
    {%- set column = column_types.get(column_name | lower) -%}
    {%- if column is not none and (column.is_float() or column.is_numeric()) -%}
        round({{ column_name }}::numeric, 4) as {{ column_name }}
    {%- else -%}
        {{ column_name }}
    {%- endif -%}
{%- endmacro %}

{% macro default__get_unit_test_sql(main_sql, expected_fixture_sql, expected_column_names) -%}
{%- set column_types = {} -%}
{%- for column in adapter.get_column_schema_from_query(main_sql) -%}
    {%- do column_types.update({column.name | lower: column}) -%}
{%- endfor -%}
-- Build actual result given inputs
with dbt_internal_unit_test_actual as (
  select
    {% for expected_column_name in expected_column_names %}{{ unit_test_compare_expression(expected_column_name, column_types) }}{% if not loop.last -%},{% endif %}{%- endfor -%}, {{ dbt.string_literal("actual") }} as {{ adapter.quote("actual_or_expected") }}
  from (
    {{ main_sql }}
  ) _dbt_internal_unit_test_actual
),
-- Build expected result
dbt_internal_unit_test_expected as (
  select
    {% for expected_column_name in expected_column_names %}{{ unit_test_compare_expression(expected_column_name, column_types) }}{% if not loop.last -%}, {% endif %}{%- endfor -%}, {{ dbt.string_literal("expected") }} as {{ adapter.quote("actual_or_expected") }}
  from (
    {{ expected_fixture_sql }}
  ) _dbt_internal_unit_test_expected
)
-- Union actual and expected results
select * from dbt_internal_unit_test_actual
union all
select * from dbt_internal_unit_test_expected
{%- endmacro %}
