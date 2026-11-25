from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import iduedu
import numpy as np
import pandas as pd
import pytest
from iduedu.graph.io import write_urban_graph
from scipy import sparse
from shapely.geometry import LineString, Point, Polygon, box

from vustbench_core import CityDataset, allocate_demand, load_catalog, load_city, load_services, run_baseline
from vustbench_core.pipeline import _save_sparse_distances, routing_boundary
from vustbench_core.aggregation import aggregate_blocks


def test_block_provision_is_demand_weighted_without_duplicate_matches(tmp_path):
    buildings = gpd.GeoDataFrame({
        "population": [100, 900, 1000], "demand": [1, 9, 100],
        "supplied_demands_within": [1, 0, 100], "demand_left": [0, 9, 0],
    }, geometry=[Point(.2, .2), Point(.3, .3), Point(2, 2)], index=["b:0", "b:1", "b:2"], crs=4326)
    blocks = gpd.GeoDataFrame({"id": ["block:0", "block:1"]}, geometry=[box(0, 0, 1, 1)] * 2,
                              index=["block:0", "block:1"], crs=4326)
    city = CityDataset(buildings, blocks, [0, 0, 1, 1], {"data_version": "fixture"})
    result = aggregate_blocks(city, buildings, tmp_path)
    assert result.loc["block:0", "demand"] == 10
    assert result.loc["block:0", "provision_value"] == pytest.approx(.1)
    assert result.loc["block:1", "demand"] == 0
    assert pd.isna(result.loc["block:1", "provision_value"])
    assert result.demand.sum() == 10
    assert buildings.demand.sum() == 110  # citywide demand is unchanged


def write_dataset(directory: Path, *, capacities: list[int | None] | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "services").mkdir(exist_ok=True)
    catalog = [{
        "name": "school", "name_ru": "Школа", "demand": 1, "accessibility": 1,
        "units": [{"capacity": value} for value in (250, 300, 600, 1100, 250, 300, 600)],
    }]
    (directory / "bn_service_types.json").write_text(json.dumps(catalog), encoding="utf-8")
    buildings = gpd.GeoDataFrame(
        {"population_balanced": [1000, 1000, 1000], "geometry": [Point(0, 0), Point(100, 0), Point(300, 0)]}, crs=3857,
    )
    buildings.to_crs(4326).to_file(directory / "buildings.geojson", driver="GeoJSON")
    blocks = gpd.GeoDataFrame(geometry=[box(-50, -50, 350, 50)], crs=3857)
    blocks.to_crs(4326).to_file(directory / "blocks.geojson", driver="GeoJSON")
    capacities = [10] if capacities is None else capacities
    services = gpd.GeoDataFrame({"capacity": capacities, "geometry": [Point(0, 0)] * len(capacities)}, crs=3857)
    services.to_crs(4326).to_file(directory / "services" / "school.geojson", driver="GeoJSON")


def write_tiny_graph(path: Path) -> None:
    nodes = gpd.GeoDataFrame(geometry=[Point(value, 0) for value in (0, 100, 200, 300)], index=[0, 1, 2, 3], crs=3857)
    edges = gpd.GeoDataFrame({
        "u": [0, 1, 2], "v": [1, 2, 3],
        "length_meter": [100.0] * 3, "time_min": [1.2] * 3,
        "geometry": [LineString([(value, 0), (value + 100, 0)]) for value in (0, 100, 200)],
    }, crs=3857)
    graph = iduedu.UrbanGraph(nodes, edges, is_multigraph=False, is_directed=False, graph_type="walk")
    write_urban_graph(graph, path)


def test_demand_conserves_city_total_and_stable_ties() -> None:
    population = pd.Series([1, 1, 1, 0], index=["b:2", "b:0", "b:1", "b:3"])
    demand = allocate_demand(population, 500)
    assert demand.sum() == 2  # half-up(1.5), not Python's bankers rounding
    assert demand.to_dict() == {"b:2": 0, "b:0": 1, "b:1": 1, "b:3": 0}
    assert demand.sort_index().equals(allocate_demand(population.iloc[::-1], 500).sort_index())
    with pytest.raises(ValueError):
        allocate_demand(pd.Series([-1], index=["b:0"]), 120)


def test_capacity_estimation_preserves_observed_and_coincident_identities(tmp_path: Path) -> None:
    write_dataset(tmp_path, capacities=[275, None, 0])
    services = load_services(tmp_path, "school")
    assert list(services.index) == ["s:school:0", "s:school:1", "s:school:2"]
    assert services.capacity.tolist() == [275, 300, 0]
    assert services.capacity_estimated.tolist() == [False, True, False]
    assert float(services.capacity_original.iloc[0]) == 275
    assert pd.isna(services.capacity_original.iloc[1])
    assert len(services) == 3  # coincident services are distinct source rows
    item = load_catalog(tmp_path)[0]
    assert item["total_capacity"] == 575
    assert item["estimated_capacity_count"] == 1
    assert item["fallback_capacity"] == 300


def test_source_population_and_identity_are_retained(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    city = load_city(tmp_path)
    assert city.buildings.index.tolist() == ["b:0", "b:1", "b:2"]
    assert city.metadata["population"] == 3000
    assert city.metadata["blocks_count"] == 1
    assert city.buildings.population_balanced.tolist() == [1000, 1000, 1000]


def test_catalog_zero_norm_for_another_type_does_not_block_school(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    path = tmp_path / "bn_service_types.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    catalog.append({"name": "farmland", "demand": 0, "accessibility": 0, "units": []})
    catalog.append({"name": "malformed", "demand": "bad", "accessibility": 0, "units": []})
    path.write_text(json.dumps(catalog), encoding="utf-8")
    assert load_services(tmp_path, "school").capacity.sum() == 10
    assert not load_catalog(tmp_path)[1]["available"]
    assert "error" in load_catalog(tmp_path)[2]


def test_sparse_archive_preserves_zero_and_infinity_semantics(tmp_path: Path) -> None:
    matrix = pd.DataFrame({"s:school:0": pd.arrays.SparseArray([0.0, np.inf, 2.0], fill_value=np.inf)}, index=["b:0", "b:1", "b:2"])
    path = tmp_path / "distances.npz"
    _save_sparse_distances(matrix, path, cutoff=3)
    saved = sparse.load_npz(path).tocoo()
    assert saved.nnz == 2
    assert list(saved.row) == [0, 2]
    assert list(saved.data) == [0, 2]
    axes = json.loads(path.with_suffix(".axes.json").read_text(encoding="utf-8"))
    assert axes["origins"] == ["b:0", "b:1", "b:2"]
    assert axes["absent_value"] == "infinity"


def test_download_envelope_tolerates_invalid_topology_without_mutating_blocks() -> None:
    crossing = Polygon([(30, 60), (30.002, 60.002), (30, 60.002), (30.002, 60), (30, 60)])
    blocks = gpd.GeoDataFrame(geometry=[crossing, crossing], crs=4326)
    before = blocks.geometry.to_wkb().tolist()
    city = CityDataset(gpd.GeoDataFrame(), blocks, list(blocks.total_bounds), {})
    territory, metadata = routing_boundary(city, threshold=15)
    assert territory.geometry.is_valid.all()
    assert territory.geometry.iloc[0].covers(crossing)
    assert metadata["buffer_m"] == 3750
    assert blocks.geometry.to_wkb().tolist() == before


def test_real_native_pipeline_distinguishes_normative_and_3t_allocation(tmp_path: Path) -> None:
    data, artifacts = tmp_path / "data", tmp_path / "artifacts"
    write_dataset(data)
    graph_path = tmp_path / "synthetic.urbangraph"
    write_tiny_graph(graph_path)
    manifest = run_baseline(data, artifacts, graph_path=graph_path, run_id="synthetic-smoke")
    assert manifest["parameters"]["routing_cutoff_minutes"] == 3
    assert manifest["summary"]["total_demand"] == 3
    assert manifest["summary"]["supplied_within"] == 1
    assert manifest["summary"]["supplied_without"] == 1
    assert manifest["summary"]["deficit_within"] == 2
    assert manifest["summary"]["unassigned_demand"] == 1
    run_dir = artifacts / "runs" / "synthetic-smoke"
    buildings = gpd.read_parquet(run_dir / "buildings.parquet")
    services = gpd.read_parquet(run_dir / "services.parquet")
    assert buildings.index.tolist() == ["b:0", "b:1", "b:2"]
    assert services.service_load.sum() == 2
    assert buildings.supplied_demands_within.sum() + buildings.supplied_demands_without.sum() == services.service_load.sum()
    assert (run_dir / "buildings.geojson").is_file()
    assert json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["status"] == "completed"
