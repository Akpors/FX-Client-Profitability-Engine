"""The synthetic client book: who the dealer's clients are and how they behave.

Each client gets a segment, a home country, an onboarding date, a trade frequency, a ticket-size
distribution, a preferred-pair profile and a pricing history flag (``legacy_deal``). Nothing
here prices a trade. The client's *expected annual EUR volume* and the volume tier it earns are
fixed up front, so a later trade-generation step can price each trade from the client's tier the
way a dealer's pricing grid would, and the volume-versus-margin pattern emerges from that grid
rather than being assigned to particular clients.

All randomness comes from the ``numpy.random.Generator`` passed in.
"""

from __future__ import annotations

from datetime import date, timedelta
from math import floor

import numpy as np
import pandas as pd

from fxengine.config import SEGMENT_NAMES, GeneratorConfig

SEGMENT_PREFIX: dict[str, str] = {
    "large_corporate": "LC",
    "mid_market": "MM",
    "sme": "SM",
    "asset_manager": "AM",
    "hedge_fund": "HF",
    "payment_institution": "PI",
}

# EU27 + GB + CH + NO, weighted roughly by the size of the local corporate and financial sector
# so the book is not as likely to be Maltese as German. Weights are relative.
HOME_COUNTRY_WEIGHTS: dict[str, float] = {
    "DE": 16, "FR": 11, "GB": 11, "NL": 8, "CH": 7, "IT": 7, "ES": 6, "IE": 4,
    "BE": 3, "AT": 3, "SE": 3, "NO": 3, "DK": 3, "LU": 3, "PL": 3, "FI": 2, "PT": 2,
    "CZ": 1, "GR": 1, "HU": 1, "RO": 1,
    "BG": 0.5, "HR": 0.5, "CY": 0.5, "EE": 0.5, "LV": 0.5, "LT": 0.5, "MT": 0.5, "SI": 0.5,
    "SK": 0.5,
}
HOME_COUNTRIES: tuple[str, ...] = tuple(HOME_COUNTRY_WEIGHTS)

COLUMNS = [
    "client_id",
    "client_name",
    "segment",
    "home_country",
    "onboarded_date",
    "legacy_deal",
    "trades_per_month",
    "ticket_mu",
    "ticket_sigma",
    "pair_weights",
    "expected_annual_volume_eur",
    "volume_tier_discount_pct",
]


def _segment_counts(cfg: GeneratorConfig) -> dict[str, int]:
    """Clients per segment by the largest-remainder method, so the mix is exact and the total
    is ``n_clients`` whatever the shares round to."""
    exact = {name: cfg.segments[name].client_share * cfg.n_clients for name in SEGMENT_NAMES}
    counts = {name: floor(value) for name, value in exact.items()}
    by_remainder = sorted(SEGMENT_NAMES, key=lambda name: exact[name] - counts[name], reverse=True)
    for name in by_remainder[: cfg.n_clients - sum(counts.values())]:
        counts[name] += 1
    return counts


def _onboarding_dates(
    n: int,
    window: tuple[date, date],
    pre_window_share: float,
    max_tenure_days: int,
    rng: np.random.Generator,
) -> tuple[list[date], np.ndarray]:
    """Onboarding dates and a flag for clients who predate the window.

    A fixed share of the segment, ``1 - pre_window_share``, is onboarded inside the window (chosen
    at random). The rest are onboarded 1 day to ``max_tenure_days`` before it starts.
    """
    start, end = window
    n_new = min(n, floor(n * (1 - pre_window_share) + 0.5))
    is_new = np.zeros(n, dtype=bool)
    is_new[rng.choice(n, size=n_new, replace=False)] = True
    days_before = rng.integers(1, max_tenure_days + 1, size=n)
    days_into = rng.integers(0, (end - start).days + 1, size=n)
    dates = [
        start + timedelta(days=int(into)) if new else start - timedelta(days=int(before))
        for new, before, into in zip(is_new, days_before, days_into, strict=True)
    ]
    return dates, ~is_new


def generate_clients(
    cfg: GeneratorConfig, window: tuple[date, date], rng: np.random.Generator
) -> pd.DataFrame:
    """Draw the client book for ``cfg`` over the trading ``window`` (first day, last day).

    Returns one row per client with the columns in ``COLUMNS``. ``pair_weights`` holds a dict per
    client. The same ``cfg``, ``window`` and generator state always give the same frame.
    """
    start, end = window
    if start > end:
        raise ValueError(f"window ends before it starts: {start} to {end}")
    book = cfg.client_book
    pairs = list(cfg.pairs)
    tier_minimums = np.array([tier.min_annual_volume_eur for tier in cfg.volume_tiers])
    tier_discounts = np.array([tier.discount_pct for tier in cfg.volume_tiers])
    country_p = np.array(list(HOME_COUNTRY_WEIGHTS.values()))
    country_p = country_p / country_p.sum()
    max_tenure_days = max(1, round(book.max_tenure_years * 365))

    counts = _segment_counts(cfg)
    frames: list[pd.DataFrame] = []
    for name in SEGMENT_NAMES:
        n = counts[name]
        if n == 0:
            continue
        seg = cfg.segments[name]
        tpm_mu, tpm_sigma = seg.trades_per_month_lognorm
        ticket_mu, ticket_sigma = seg.ticket_eur_lognorm

        trades_per_month = rng.lognormal(tpm_mu, tpm_sigma, size=n)
        client_ticket_mu = rng.normal(ticket_mu, book.ticket_mu_sd, size=n)
        client_ticket_sigma = np.full(n, ticket_sigma)

        onboarded, pre_window = _onboarding_dates(
            n, window, book.pre_window_share, max_tenure_days, rng
        )
        # A legacy discount is an old deal never repriced, so only an existing relationship
        # can hold one. The draw is made for everyone so the stream does not depend on who is new.
        legacy_deal = (rng.random(n) < cfg.legacy_deal.probability) & pre_window

        home_country = rng.choice(HOME_COUNTRIES, size=n, p=country_p)

        popularity = np.array([seg.pair_popularity[pair] for pair in pairs], dtype=float)
        alpha = book.pair_concentration * popularity / popularity.sum()
        weights = rng.dirichlet(alpha, size=n)
        pair_weights = [dict(zip(pairs, row.tolist(), strict=True)) for row in weights]

        volume = trades_per_month * 12 * np.exp(client_ticket_mu + client_ticket_sigma**2 / 2)
        tier = np.searchsorted(tier_minimums, volume, side="right") - 1

        frames.append(
            pd.DataFrame(
                {
                    "segment": name,
                    "home_country": home_country,
                    "onboarded_date": onboarded,
                    "legacy_deal": legacy_deal,
                    "trades_per_month": trades_per_month,
                    "ticket_mu": client_ticket_mu,
                    "ticket_sigma": client_ticket_sigma,
                    "pair_weights": pair_weights,
                    "expected_annual_volume_eur": volume,
                    "volume_tier_discount_pct": tier_discounts[tier],
                }
            )
        )

    clients = pd.concat(frames, ignore_index=True)
    number = clients.groupby("segment", sort=False).cumcount() + 1
    prefix = clients["segment"].map(SEGMENT_PREFIX)
    clients["client_id"] = prefix + "-" + number.astype(str).str.zfill(3)
    clients["client_name"] = "Client " + clients["client_id"]
    return clients[COLUMNS]
