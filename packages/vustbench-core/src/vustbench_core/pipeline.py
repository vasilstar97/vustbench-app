"""A real IduEdu → ObjectNat baseline with replayable local artifacts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import geopandas as gpd
import iduedu
import numpy as np
import objectnat
import pandas as pd
from iduedu.graph.io import read_urban_graph, write_urban_graph
from scipy import sparse
from shapely.geometry import GeometryCollection

from .catalog import CAPACITY_POLICY, catalog_service, fallback_capacity
from .aggregation import aggregate_blocks
from .datasets import CityDataset, fingerprint, load_city, load_services
from .demand import allocate_demand
from .native import ObjectNatDistanceFrame, finite_coo

WALK_SPEED_M_PER_MIN = 5000 / 60
Progress = Callable[[str, str], None]
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")


def _report(progress: Progress | None, stage: str, message: str) -> None:
    if progress is not None:
        progress(stage, message)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        if name == "vustbench-core":
            return "0.1.0+source"
        raise


def _json_write(path: Path, value: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def routing_boundary(city: CityDataset, threshold: float) -> tuple[gpd.GeoDataFrame, dict[str, Any]]:
    """Hull source blocks and buffer in a local metric CRS.

    The hull deliberately fills holes and gaps for a bounded Overpass query;
    it is a routing download envelope rather than a city administrative border.
    Buffer length matches the maximum route considered by ObjectNat (3T).
    """
    metric_crs = city.blocks.estimate_utm_crs()
    if metric_crs is None:
        raise ValueError("Could not determine a metric CRS for the city boundary")
    blocks = city.blocks.to_crs(metric_crs)
    buffer_m = max(1000.0, 3 * threshold * WALK_SPEED_M_PER_MIN)
    # Overlay union can fail when projection introduces tiny topological
    # conflicts between repaired polygon pieces. The final download envelope
    # only needs their convex hull: it gives the same hull of the buffered
    # union without performing a polygon overlay or changing source geometry.
    envelope = GeometryCollection(list(blocks.geometry)).convex_hull.buffer(buffer_m).convex_hull
    territory = gpd.GeoDataFrame(geometry=[envelope], crs=metric_crs).to_crs(4326)
    return territory, {
        "policy": "blocks_collection_convex_hull_metric_buffer",
        "buffer_m": buffer_m,
        "metric_crs": str(metric_crs),
        "bounds4326": [float(value) for value in territory.total_bounds],
    }


def _get_graph(
    city: CityDataset,
    artifacts_dir: Path,
    threshold: float,
    graph_path: Path | None,
    osm_id: int | None,
    progress: Progress | None,
) -> tuple[iduedu.UrbanGraph, dict[str, Any]]:
    if graph_path is not None:
        path = Path(graph_path).resolve()
        _report(progress, "graph", f"Чтение сохранённого графа: {path.name}")
        graph = read_urban_graph(path)
        return graph, {"source": "provided_archive", "path": str(path), "sha256": fingerprint([path]), "mode": "walk"}
    territory, boundary_meta = routing_boundary(city, threshold)
    key = hashlib.sha256(
        json.dumps({"iduedu": version("iduedu"), "osm_id": osm_id, "mode": "walk", "boundary": boundary_meta}, sort_keys=True).encode()
        + territory.geometry.iloc[0].wkb
    ).hexdigest()[:24]
    graph_dir = artifacts_dir / "graphs"
    graph_dir.mkdir(parents=True, exist_ok=True)
    archive = graph_dir / f"walk-{key}.urbangraph"
    if archive.is_file():
        _report(progress, "graph", "Чтение пешеходного графа из локального кэша")
        graph = read_urban_graph(archive)
        source = "cache"
    else:
        _report(progress, "graph", "Загрузка пешеходного графа через IduEdu / OpenStreetMap; это может занять несколько минут")
        arguments: dict[str, Any] = {
            "simplify": True,
            "keep_largest_subgraph": False,
            "walk_speed": WALK_SPEED_M_PER_MIN,
        }
        if osm_id is None:
            arguments["territory"] = territory
        else:
            arguments["osm_id"] = osm_id
        graph = iduedu.get_walk_graph(**arguments)
        _report(progress, "graph", "Сохранение исходного пешеходного графа")
        # Only a successful native archive is reused; partial writes use a
        # different filename and are never mistaken for completed downloads.
        temporary = graph_dir / f"walk-{key}.partial.urbangraph"
        write_urban_graph(graph, temporary)
        temporary.replace(archive)
        source = "osm_download"
        _json_write(archive.with_suffix(".json"), {"created_at": datetime.now(timezone.utc).isoformat(), "boundary": boundary_meta, "osm_id": osm_id, "iduedu": version("iduedu")})
    return graph, {"source": source, "path": str(archive.resolve()), "mode": "walk", "osm_id": osm_id, "boundary": boundary_meta}


def _save_sparse_distances(matrix: pd.DataFrame, path: Path, *, cutoff: float) -> None:
    """Persist finite pairs, including explicit zeroes, without densifying.

    scipy treats an absent sparse entry as zero; our matrix metadata explicitly
    defines absent entries as infinity instead. Consumers must use the axis
    file and this convention rather than calling toarray() and assuming zero.
    """
    saved = finite_coo(matrix).tocsr()
    sparse.save_npz(path, saved)
    _json_write(path.with_suffix(".axes.json"), {
        "origins": [str(value) for value in matrix.index],
        "destinations": [str(value) for value in matrix.columns],
        "orientation": "buildings_by_services",
        "unit": "minutes",
        "cutoff": cutoff,
        "absent_value": "infinity",
        "explicit_zero_values_are_reachable": True,
    })


def _merge_results(
    buildings: gpd.GeoDataFrame,
    services: gpd.GeoDataFrame,
    provision: Any,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    building_columns = ["id", "population_balanced", "population", "demand", "geometry"]
    result_buildings = buildings[building_columns].copy()
    for column in provision.demand_rows.columns:
        if column != "demand":
            result_buildings[column] = provision.demand_rows[column].reindex(result_buildings.index)
    for column in ("supplied_demands_within", "supplied_demands_without"):
        result_buildings[column] = result_buildings.get(column, pd.Series(0, index=buildings.index)).fillna(0).astype("int64")
    result_buildings["demand_left"] = result_buildings.get("demand_left", buildings.demand).fillna(buildings.demand).astype("int64")
    # An object without demand has no defined provision ratio.
    result_buildings["provision_value"] = np.where(
        result_buildings.demand > 0,
        result_buildings.supplied_demands_within / result_buildings.demand.replace(0, np.nan),
        np.nan,
    )
    result_buildings = result_buildings.replace([np.inf, -np.inf], np.nan)
    service_columns = ["id", "service_type", "capacity", "capacity_original", "capacity_estimated", "capacity_source", "geometry"]
    result_services = services[service_columns].copy()
    for column in provision.capacity_rows.columns:
        if column != "capacity":
            result_services[column] = provision.capacity_rows[column].reindex(result_services.index)
    result_services["capacity_left"] = result_services.get("capacity_left", services.capacity).fillna(services.capacity).astype("int64")
    result_services["service_load"] = result_services.get("service_load", pd.Series(0, index=services.index)).fillna(0).astype("int64")
    for column in ("carried_capacity_within", "carried_capacity_without"):
        if column in result_services:
            result_services[column] = result_services[column].fillna(0).astype("int64")
    result_services["load_ratio"] = np.where(result_services.capacity > 0, result_services.service_load / result_services.capacity.replace(0, np.nan), np.nan)
    return result_buildings, result_services


def run_baseline(
    data_dir: Path,
    artifacts_dir: Path,
    service_name: str = "school",
    mode: str = "walk",
    run_id: str | None = None,
    progress: Progress | None = None,
    graph_path: Path | None = None,
    osm_id: int | None = None,
    export_geojson: bool = True,
) -> dict[str, Any]:
    """Calculate a real baseline; a missing graph triggers IduEdu download.

    Only walking has a verified MVP contract. Supplied graph archives are
    intended for reproducible calculations and explicit synthetic smoke tests.
    """
    if mode != "walk":
        raise ValueError("Only mode='walk' is supported by the MVP calculation")
    identifier = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-") + uuid4().hex[:8]
    if not RUN_ID.fullmatch(identifier):
        raise ValueError("Invalid run ID")
    data_dir, artifacts_dir = Path(data_dir), Path(artifacts_dir)
    run_dir = artifacts_dir / "runs" / identifier
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file() and json.loads(manifest_path.read_text(encoding="utf-8")).get("status") == "completed":
        raise ValueError(f"Run {identifier!r} is already completed")
    run_dir.mkdir(parents=True, exist_ok=True)
    _report(progress, "loading", "Чтение исходных зданий, кварталов и каталога сервисов")
    city = load_city(data_dir)
    norm = catalog_service(data_dir, service_name)
    services = load_services(data_dir, service_name)
    buildings = city.buildings.copy()
    buildings["demand"] = allocate_demand(buildings.population_balanced, norm["demand"])
    active_buildings = buildings.loc[buildings.demand > 0].copy()
    active_services = services.loc[services.capacity > 0].copy()
    if active_buildings.empty or active_services.empty:
        raise ValueError("Provision requires positive demand and at least one positive-capacity service")
    threshold = float(norm["accessibility"])
    if threshold <= 0:
        raise ValueError("Provision requires a positive accessibility norm in minutes")
    cutoff = 3 * threshold
    graph, graph_metadata = _get_graph(city, artifacts_dir, threshold, graph_path, osm_id, progress)
    if graph.type not in (None, "walk"):
        raise ValueError(f"Walking calculation cannot use a graph with type {graph.type!r}")
    if graph.nodes_gdf.empty or graph.edges_gdf.empty:
        raise ValueError("The routing graph has no nodes or edges")
    graph_metadata.update(nodes_count=len(graph.nodes_gdf), edges_count=len(graph.edges_gdf), graph_type=graph.type)
    _report(progress, "projection", f"Привязка {len(active_buildings)} зданий и {len(active_services)} сервисов к рёбрам графа")
    objects = gpd.GeoDataFrame(
        pd.concat([active_buildings[["geometry"]], active_services[["geometry"]]]),
        geometry="geometry",
        crs=buildings.crs,
    )
    projected_graph, node_map = graph.project_objects(objects, WALK_SPEED_M_PER_MIN, add_link_edge=True)
    if node_map.isna().any() or not objects.index.isin(node_map.index).all():
        raise ValueError("IduEdu could not project every building and service onto the routing graph")
    active_buildings["graph_node_id"] = node_map.reindex(active_buildings.index)
    active_services["graph_node_id"] = node_map.reindex(active_services.index)
    _report(progress, "routing", f"Расчёт разреженной матрицы времени; порог {cutoff:g} минут (3T)")
    matrix = iduedu.od_matrix(
        projected_graph,
        gdf_origins=active_buildings,
        gdf_destinations=active_services,
        weight="time_min",
        cutoff=cutoff,
        output="distances",
        distance_format="frame",
    )
    if not isinstance(matrix, pd.DataFrame):
        raise TypeError("IduEdu returned an unexpected OD matrix representation")
    if not matrix.index.equals(active_buildings.index) or not matrix.columns.equals(active_services.index):
        raise ValueError("OD matrix axes do not match building and service IDs")
    _save_sparse_distances(matrix, run_dir / "distances.npz", cutoff=cutoff)
    _report(progress, "provision", "Расчёт обеспеченности через ObjectNat с seed=0")
    provision = objectnat.get_service_provision(
        buildings=active_buildings,
        distance_matrix=ObjectNatDistanceFrame(matrix),
        services=active_services,
        threshold=threshold,
        buildings_demand_column="demand",
        services_capacity_column="capacity",
        seed=0,
    )
    result_buildings, result_services = _merge_results(buildings, services, provision)
    _report(progress, "saving", "Сохранение результатов, матрицы и описания расчёта")
    result_buildings.to_parquet(run_dir / "buildings.parquet")
    result_services.to_parquet(run_dir / "services.parquet")
    aggregate_blocks(city, result_buildings, artifacts_dir).to_parquet(run_dir / "blocks.parquet")
    if export_geojson:
        result_buildings.to_file(run_dir / "buildings.geojson", driver="GeoJSON", index=False)
        result_services.to_file(run_dir / "services.geojson", driver="GeoJSON", index=False)
    demand = int(result_buildings.demand.sum())
    within = int(result_buildings.supplied_demands_within.sum())
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "id": identifier,
        "status": "completed",
        "service": service_name,
        "service_name_ru": norm.get("name_ru", service_name),
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "data_version": fingerprint([data_dir / "buildings.geojson", data_dir / "blocks.geojson", data_dir / "bn_service_types.json", data_dir / "services" / f"{service_name}.geojson"]),
        "city": city.metadata,
        "summary": {
            "population": float(buildings.population.sum()),
            "total_demand": demand,
            "total_capacity": int(services.capacity.sum()),
            "supplied_within": within,
            "supplied_without": int(result_buildings.supplied_demands_without.sum()),
            "provision_ratio": within / demand if demand else None,
            "deficit_within": demand - within,
            "unassigned_demand": int(result_buildings.demand_left.sum()),
            "services_count": len(services),
            "buildings_count": len(buildings),
            "routed_buildings_count": len(active_buildings),
            "estimated_capacity_count": int(services.capacity_estimated.sum()),
        },
        "parameters": {
            "demand_per_1000": norm["demand"],
            "threshold_minutes": threshold,
            "routing_cutoff_minutes": cutoff,
            "routing_weight": "time_min",
            "walk_speed_m_per_min": WALK_SPEED_M_PER_MIN,
            "seed": 0,
            "demand_rounding": "largest_remainders; total_half_up; stable_id_ties",
            "capacity_estimation_policy": CAPACITY_POLICY,
            "fallback_capacity": fallback_capacity(norm),
            "projection": "IduEdu project_objects; representative points; explicit walking connector edges",
            "identity_policy": "dataset_and_source_row; coincident_geometries_retained",
            "geometry_policy": "make_valid_preserving_source_row; null_empty_raises",
            "services_geometry_repaired": services.attrs.get("geometry_repaired_count", 0),
        },
        "versions": {name: _package_version(name) for name in ("vustbench-core", "iduedu", "objectnat", "geopandas", "shapely")},
        "graph": graph_metadata,
        "artifacts": {
            "buildings": "buildings.parquet",
            "services": "services.parquet",
            "blocks": "blocks.parquet",
            **({"buildings_geojson": "buildings.geojson", "services_geojson": "services.geojson"} if export_geojson else {}),
            "distances": "distances.npz",
            "distance_axes": "distances.axes.json",
        },
    }
    _json_write(manifest_path, manifest)
    _report(progress, "completed", "Расчёт завершён")
    return manifest
