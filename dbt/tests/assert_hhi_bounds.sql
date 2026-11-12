{#-
  The Herfindahl-Hirschman Index of a population of n clients, on the 0-1 scale (sum of squared
  shares), has hard bounds:

      1 / n  <=  hhi  <=  1

  The floor is a book split into n equal shares; the ceiling is one client holding everything.
  Returns each measure in mart_concentration that breaks either bound; 0 rows is a pass. The
  1e-9 tolerance on the floor absorbs the last digit of exact-decimal division (n equal shares
  of 1/n do not add back to exactly 1).

  The bounds only hold when every share is between 0 and 1, which is why the revenue and
  contribution measures are built over positive values only (metrics.md section 6).
-#}
select
    measure,
    n_clients,
    hhi
from {{ ref('mart_concentration') }}
where hhi < 1.0 / n_clients - 1e-9
    or hhi > 1
