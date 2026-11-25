"""Weighted building-to-block provision without duplicating boundary matches."""

from pathlib import Path

import geopandas as gpd
import pandas as pd

from .datasets import CityDataset


def building_blocks(city: CityDataset, artifacts_dir: Path) -> pd.Series:
    cache = Path(artifacts_dir) / "inputs" / city.metadata["data_version"] / "building_blocks.parquet"
    if cache.is_file():
        return pd.read_parquet(cache)["block_id"].reindex(city.buildings.index)
    points = city.buildings[["geometry"]].copy()
    points.geometry = points.geometry.representative_point()
    points["building_id"] = points.index
    blocks = city.blocks[["geometry"]].copy()
    blocks["block_id"] = blocks.index
    blocks["block_order"] = range(len(blocks))
    joined = gpd.sjoin(points, blocks, how="left", predicate="intersects")
    # Overlapping/duplicate blocks must not count the same building twice.
    joined = joined.sort_values("block_order", kind="stable", na_position="last")
    membership = joined.drop_duplicates("building_id").set_index("building_id")["block_id"]
    membership = membership.reindex(city.buildings.index)
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".tmp.parquet")
    membership.to_frame().to_parquet(temporary)
    temporary.replace(cache)
    return membership


def aggregate_blocks(city: CityDataset, buildings: gpd.GeoDataFrame, artifacts_dir: Path) -> gpd.GeoDataFrame:
    membership = building_blocks(city, artifacts_dir)
    columns = ["population", "demand", "supplied_demands_within", "demand_left"]
    values = buildings[columns].copy()
    values["block_id"] = membership
    sums = values.groupby("block_id")[columns].sum()
    result = city.blocks[["id", "geometry"]].join(sums)
    result[columns] = result[columns].fillna(0)
    result["provision_value"] = result["supplied_demands_within"].div(result["demand"].where(result["demand"] > 0))
    return result
