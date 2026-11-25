"""Integer demand with citywide conservation and deterministic tie breaking."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP

import pandas as pd


def allocate_demand(population: pd.Series, demand_per_1000: int | float) -> pd.Series:
    """Largest remainders; the sum is half-up rounded total population × norm.

    Ties are resolved by stable source IDs rather than the current row order.
    ObjectNat consumes integers; rounding each small building separately would
    systematically lose demand, so rounding is performed at city scale.
    """
    if not population.index.is_unique:
        raise ValueError("Demand allocation requires unique source IDs")
    try:
        norm = Decimal(str(demand_per_1000)) / Decimal(1000)
        values = [Decimal(str(value)) for value in population]
    except InvalidOperation as error:
        raise ValueError("Population and demand norm must be numeric") from error
    if not norm.is_finite() or norm < 0 or any(not value.is_finite() or value < 0 for value in values):
        raise ValueError("Population and demand norm must be finite and nonnegative")
    quotas = [value * norm for value in values]
    allocated = [int(value.to_integral_value(rounding=ROUND_FLOOR)) for value in quotas]
    target = int(sum(quotas, Decimal(0)).to_integral_value(rounding=ROUND_HALF_UP))
    remaining = target - sum(allocated)
    order = sorted(
        range(len(quotas)),
        key=lambda row: (-(quotas[row] - allocated[row]), str(population.index[row])),
    )
    for row in order[:remaining]:
        allocated[row] += 1
    return pd.Series(allocated, index=population.index, name="demand", dtype="int64")
