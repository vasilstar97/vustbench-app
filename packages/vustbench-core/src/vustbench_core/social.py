"""Configurable social support dependency graph, independent of the application.

Missing inputs stay NaN. Weighted means renormalize available weights, retaining
their original coverage; formula (46) requires all four time-use dimensions.
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class SupportResult:
    values: pd.DataFrame
    coverage: pd.DataFrame


def evaluate_support(nodes: list[dict[str, Any]], inputs: dict[str, pd.Series], index: pd.Index) -> SupportResult:
    definitions = {node["id"]: node for node in nodes}
    if len(definitions) != len(nodes):
        raise ValueError("Social support node ids must be unique")
    values, coverage, visiting = {}, {}, set()

    def evaluate(identifier: str):
        if identifier in values:
            return
        if identifier in visiting:
            raise ValueError("Cyclic social support dependencies")
        if identifier not in definitions:
            raise ValueError(f"Unknown social support dependency: {identifier}")
        visiting.add(identifier)
        node = definitions[identifier]
        operation = node["operation"]
        if operation == "service":
            value = inputs.get(node["service"], pd.Series(np.nan, index=index)).reindex(index).astype(float)
            if ((value.dropna() < 0) | (value.dropna() > 1)).any() or np.isinf(value).any():
                raise ValueError("Service support must be finite and within [0, 1], or NaN")
            available = value.notna().astype(float)
        elif operation == "unavailable":
            value, available = pd.Series(np.nan, index=index), pd.Series(0.0, index=index)
        elif operation in {"weighted_mean", "k_sd"}:
            dependencies = node["dependencies"]
            if not dependencies:
                raise ValueError("Composite support nodes require dependencies")
            for dependency in dependencies:
                evaluate(dependency["id"])
            weights = np.array([float(d.get("weight", 1)) for d in dependencies])
            if not np.isfinite(weights).all() or (weights <= 0).any():
                raise ValueError("Support weights must be finite and positive")
            components = pd.concat([values[d["id"]] for d in dependencies], axis=1)
            covered = pd.concat([coverage[d["id"]] for d in dependencies], axis=1)
            if operation == "weighted_mean":
                denominator = components.notna().mul(weights, axis=1).sum(axis=1)
                value = components.mul(weights, axis=1).sum(axis=1).div(denominator.where(denominator > 0))
                available = covered.mul(weights, axis=1).sum(axis=1) / weights.sum()
            else:
                if len(dependencies) != 4:
                    raise ValueError("K_SD requires work, leisure, development and risk, in this order")
                value = (components.iloc[:, 0] + components.iloc[:, 1]) * (components.iloc[:, 2] + components.iloc[:, 3]) / 4
                available = covered.mean(axis=1)
        else:
            raise ValueError(f"Unknown support operation: {operation}")
        values[identifier], coverage[identifier] = value, available
        visiting.remove(identifier)

    for identifier in definitions:
        evaluate(identifier)
    return SupportResult(pd.DataFrame(values, index=index), pd.DataFrame(coverage, index=index))


def summarize_support(nodes: list[dict[str, Any]], service_totals: dict[str, tuple[float, float]]) -> dict[str, dict[str, float | None]]:
    """Evaluate the graph at the target territory scale from (demand, supplied) sums.

Never average block provision ratios or block criteria. The same graph is
evaluated separately for each spatial scale using that scale's service totals.
"""
    index = pd.Index(["territory"])
    inputs = {}
    for service, (demand, supplied) in service_totals.items():
        if not np.isfinite([demand, supplied]).all() or not 0 <= supplied <= demand:
            raise ValueError("Service totals require finite 0 <= supplied <= demand")
        inputs[service] = pd.Series([supplied / demand if demand > 0 else np.nan], index=index)
    result = evaluate_support(nodes, inputs, index)
    return {identifier: {
        "value": float(result.values.loc["territory", identifier]) if pd.notna(result.values.loc["territory", identifier]) else None,
        "coverage": float(result.coverage.loc["territory", identifier]),
    } for identifier in result.values}
