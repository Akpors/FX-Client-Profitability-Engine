{#-
  Use the custom schema name exactly as written in dbt_project.yml.

  dbt's default prefixes the target schema (public_staging, public_marts, ...). Here the models
  must land in the schemas `staging`, `intermediate` and `marts`, because the dashboard and the
  published queries read `marts.<table>` by that name.
-#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
