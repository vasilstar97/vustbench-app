"""Read the supplied catalog; never infer a service norm from its geometry."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

CAPACITY_POLICY = "lower_median_catalog_unit"
SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def service_path(data_dir: Path, service_name: str) -> Path:
    if not SERVICE_NAME.fullmatch(service_name):
        raise ValueError("Service name must contain only lowercase letters, digits and underscores")
    return Path(data_dir) / "services" / f"{service_name}.geojson"


def read_catalog(data_dir: Path) -> list[dict[str, Any]]:
    with (Path(data_dir) / "bn_service_types.json").open(encoding="utf-8-sig") as source:
        catalog = json.load(source)
    if not isinstance(catalog, list):
        raise ValueError("bn_service_types.json must contain a list")
    names: set[str] = set()
    for item in catalog:
        if not isinstance(item, dict):
            raise ValueError("Every service catalog item must be an object")
        name = item.get("name", "")
        service_path(data_dir, name)
        if name in names:
            raise ValueError(f"Duplicate service name in catalog: {name}")
        names.add(name)
    return catalog


def validate_norms(item: dict[str, Any]) -> None:
    for field in ("demand", "accessibility"):
        try:
            value = float(item[field])
        except (TypeError, ValueError, KeyError) as error:
            raise ValueError(f"Invalid {field} for service {item['name']}") from error
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Provision requires positive {field} for service {item['name']}")


def catalog_service(data_dir: Path, name: str) -> dict[str, Any]:
    for item in read_catalog(data_dir):
        if item["name"] == name:
            validate_norms(item)
            return item
    raise ValueError(f"Service {name!r} is not present in the supplied service catalog")


def fallback_capacity(service: dict[str, Any]) -> int:
    """Lower median of all positive catalog units, including repeated capacities.

    Repeated units describe separate catalog building types and are retained.
    Picking an actual unit avoids inventing a type halfway between two units.
    The geometry is not used: building footprints do not identify floor area.
    """
    capacities = []
    for unit in service.get("units", []):
        value = float(unit.get("capacity", 0))
        if math.isfinite(value) and value > 0 and value.is_integer():
            capacities.append(int(value))
    if not capacities:
        raise ValueError(f"No positive integer catalog capacity for {service['name']}")
    capacities.sort()
    return capacities[(len(capacities) - 1) // 2]


def normalize_capacity(value: Any, fallback: int, *, identity: str) -> tuple[int, bool]:
    if value is None:
        return fallback, True
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid capacity for {identity}: {value!r}") from error
    if math.isnan(number):
        return fallback, True
    if not math.isfinite(number) or number < 0 or not number.is_integer():
        raise ValueError(f"Capacity must be a nonnegative integer for {identity}: {value!r}")
    return int(number), False


def load_catalog(data_dir: Path) -> list[dict[str, Any]]:
    """Catalog DTOs, including input readiness and capacity provenance.

    File inspection is deliberately independent of GeoPandas, so the API can
    show catalog availability without loading every geometry into memory.
    """
    result = []
    for source_item in read_catalog(Path(data_dir)):
        item = dict(source_item)
        path = service_path(data_dir, item["name"])
        item.update(
            available=path.is_file(),
            service_count=0,
            source_count=0,
            total_capacity=0,
            estimated_capacity_count=0,
            capacity_estimation_policy=CAPACITY_POLICY,
        )
        try:
            validate_norms(item)
            fallback = fallback_capacity(item)
            item["fallback_capacity"] = fallback
            if path.is_file():
                with path.open(encoding="utf-8-sig") as source:
                    features = json.load(source)["features"]
                item["service_count"] = item["source_count"] = len(features)
                for row, feature in enumerate(features):
                    capacity, estimated = normalize_capacity(
                        (feature.get("properties") or {}).get("capacity"),
                        fallback,
                        identity=f"s:{item['name']}:{row}",
                    )
                    item["total_capacity"] += capacity
                    item["estimated_capacity_count"] += int(estimated)
        except (ValueError, KeyError, TypeError) as error:
            item["available"] = False
            item["error"] = str(error)
        result.append(item)
    return result
