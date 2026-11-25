"""Dataset adapters and identity, independent from the calculation engine."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import make_valid

from .catalog import CAPACITY_POLICY, catalog_service, fallback_capacity, normalize_capacity, service_path


@dataclass
class CityDataset:
    buildings: gpd.GeoDataFrame
    blocks: gpd.GeoDataFrame
    bounds4326: list[float]
    metadata: dict[str, Any]


def fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def read_geometry(path: Path, prefix: str) -> gpd.GeoDataFrame:
    """Preserve one source row per object, including coincident geometries.

    Invalid geometries are repaired with GEOS make_valid without changing row
    identities. Null/empty geometries fail explicitly; they are never dropped.
    """
    frame = gpd.read_file(path)
    if frame.empty:
        raise ValueError(f"Input layer is empty: {path.name}")
    frame.index = pd.Index([f"{prefix}:{row}" for row in range(len(frame))], name="feature_id")
    frame["id"] = frame.index
    if frame.crs is None:
        # GeoJSON RFC 7946 uses CRS84 when an explicit CRS is absent.
        frame = frame.set_crs(4326)
    frame = frame.to_crs(4326)
    missing = frame.geometry.isna() | frame.geometry.is_empty
    if missing.any():
        raise ValueError(f"Null/empty geometry in {path.name}: {frame.index[missing].tolist()[:5]}")
    invalid = ~frame.geometry.is_valid
    repaired_count = int(invalid.sum())
    if repaired_count:
        frame.loc[invalid, "geometry"] = frame.loc[invalid].geometry.map(make_valid)
    invalid_after = frame.geometry.isna() | frame.geometry.is_empty | ~frame.geometry.is_valid
    if invalid_after.any():
        raise ValueError(f"Geometry repair failed in {path.name}: {frame.index[invalid_after].tolist()[:5]}")
    frame.attrs["geometry_repaired_count"] = repaired_count
    frame.attrs["source_path"] = str(path)
    return frame


def load_city(data_dir: Path) -> CityDataset:
    data_dir = Path(data_dir)
    buildings = read_geometry(data_dir / "buildings.geojson", "b")
    blocks = read_geometry(data_dir / "blocks.geojson", "block")
    if "population_balanced" not in buildings:
        raise ValueError("Buildings require population_balanced")
    population = pd.to_numeric(buildings["population_balanced"], errors="coerce")
    if population.isna().any() or not np.isfinite(population).all() or (population < 0).any():
        raise ValueError("population_balanced must be finite, nonnegative, and complete")
    buildings["population_balanced"] = population
    buildings["population"] = population
    bounds = [float(value) for value in blocks.total_bounds]
    metadata = {
        "name": "Санкт-Петербург",
        "population": float(population.sum()),
        "building_count": len(buildings),
        "populated_building_count": int((population > 0).sum()),
        "blocks_count": len(blocks),
        "bounds4326": bounds,
        "data_version": fingerprint([data_dir / "buildings.geojson", data_dir / "blocks.geojson"]),
        "identity_policy": "source_dataset_and_row; coincident geometries retained",
        "geometry_policy": "make_valid preserving row identity; null/empty raises",
        "buildings_geometry_repaired": buildings.attrs.get("geometry_repaired_count", 0),
        "blocks_geometry_repaired": blocks.attrs.get("geometry_repaired_count", 0),
    }
    return CityDataset(buildings, blocks, bounds, metadata)


def load_services(data_dir: Path, service_name: str) -> gpd.GeoDataFrame:
    item = catalog_service(data_dir, service_name)
    fallback = fallback_capacity(item)
    frame = read_geometry(service_path(data_dir, service_name), f"s:{service_name}")
    original = frame.get("capacity", pd.Series(None, index=frame.index, dtype=object))
    normalized = [
        normalize_capacity(value, fallback, identity=str(identity))
        for identity, value in original.items()
    ]
    # Keep the supplied values separately. Numeric strings remain strings, so
    # the original values are unambiguous and Parquet has a stable schema.
    frame["capacity_original"] = original.map(lambda value: None if pd.isna(value) else str(value)).astype("string")
    frame["capacity"] = pd.Series([value for value, _ in normalized], index=frame.index, dtype="int64")
    frame["capacity_estimated"] = [estimated for _, estimated in normalized]
    frame["capacity_source"] = np.where(frame.capacity_estimated, CAPACITY_POLICY, "supplied")
    frame["service_type"] = service_name
    frame.attrs.update(
        capacity_estimation_policy=CAPACITY_POLICY,
        fallback_capacity=fallback,
        estimated_capacity_count=int(frame.capacity_estimated.sum()),
    )
    return frame
