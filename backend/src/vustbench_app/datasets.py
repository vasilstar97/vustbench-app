"""Cached geospatial input/result views; no evaluation model lives here."""

from collections import OrderedDict
import json
from threading import RLock
from typing import Any

import geopandas as gpd
from shapely.geometry import box

from .runs import RunStore
from .settings import Settings


MIN_BUILDING_ZOOM = 12


class DatasetStore:
    def __init__(self, settings: Settings, runs: RunStore):
        self.settings = settings
        self.runs = runs
        self._city = None
        self._catalog: list[dict[str, Any]] | None = None
        self._frames: OrderedDict[tuple, gpd.GeoDataFrame] = OrderedDict()
        self._lock = RLock()

    @property
    def city(self):
        with self._lock:
            if self._city is None:
                from vustbench_core import load_city

                self._city = load_city(self.settings.data_dir)
            return self._city

    def city_metadata(self) -> dict[str, Any]:
        return {**self.city.metadata, "bounds": list(self.city.bounds4326)}

    def catalog(self) -> list[dict[str, Any]]:
        with self._lock:
            if self._catalog is None:
                from vustbench_core import load_catalog

                self._catalog = load_catalog(self.settings.data_dir)
            return self._catalog

    def _frame(self, layer: str, service: str, run_id: str | None) -> gpd.GeoDataFrame:
        # Result records are immutable after completion; only cache completed frames.
        key = (layer, service if layer == "services" else None, run_id)
        with self._lock:
            if key in self._frames:
                self._frames.move_to_end(key)
                return self._frames[key]
            if run_id and (layer != "blocks" or (self.runs.directory(run_id) / "blocks.parquet").is_file()):
                frame = gpd.read_parquet(self.runs.directory(run_id) / f"{layer}.parquet")
            elif layer == "buildings":
                frame = self.city.buildings
            elif layer == "blocks":
                frame = self.city.blocks
            else:
                from vustbench_core import load_services

                frame = load_services(self.settings.data_dir, service)
            frame = frame.to_crs(4326).copy()
            if "id" not in frame:
                frame["id"] = frame.index.astype(str)
            if layer == "buildings" and "population" not in frame and "population_balanced" in frame:
                frame["population"] = frame["population_balanced"]
            # Index construction is cached, rather than repeated per viewport.
            _ = frame.sindex
            self._frames[key] = frame
            while len(self._frames) > 8:
                self._frames.popitem(last=False)
            return frame

    def layer(
        self, layer: str, service: str, run_id: str | None, bbox: tuple[float, float, float, float] | None,
        zoom: float,
        value_field: str | None = None,
    ) -> dict[str, Any]:
        if layer == "buildings" and zoom < MIN_BUILDING_ZOOM:
            return self._collection([], 0, zoom, min_zoom=MIN_BUILDING_ZOOM)
        frame = self._frame(layer, service, run_id)
        return self.frame_collection(frame, layer, bbox, zoom, value_field)

    def frame_collection(self, frame: gpd.GeoDataFrame, layer: str, bbox, zoom: float,
                         value_field: str | None = None, extra_fields: list[str] | None = None) -> dict[str, Any]:
        if bbox is not None:
            indices = frame.sindex.query(box(*bbox), predicate="intersects")
            frame = frame.iloc[indices]
        matched = len(frame)
        frame = frame.iloc[: self.settings.max_layer_features]
        fields = {
            "buildings": ["id", "population", "demand", "provision_value", "supplied_demands_within", "demand_left"],
            "services": ["id", "capacity", "capacity_estimated", "capacity_source", "service_load", "load_ratio"],
            "blocks": ["id", "population", "demand", "supplied_demands_within", "demand_left", "provision_value"],
        }[layer]
        if value_field and value_field not in fields:
            fields.append(value_field)
        fields.extend(extra_fields or [])
        frame = frame[[column for column in fields if column in frame] + [frame.geometry.name]].copy()
        if layer == "services":
            # Facilities can be footprints; the map displays service markers.
            # Keep the original source/result geometries in the cache and on disk.
            frame.geometry = frame.geometry.representative_point()
        result = json.loads(frame.to_json(na="null", drop_id=True))
        result["metadata"] = self._metadata(len(result["features"]), matched, zoom)
        return result

    def _metadata(self, returned: int, matched: int, zoom: float, **extra) -> dict[str, Any]:
        return {
            "returned_count": returned,
            "matched_count": matched,
            "limit": self.settings.max_layer_features,
            "truncated": matched > returned,
            "zoom": zoom,
            **extra,
        }

    def _collection(self, features: list, matched: int, zoom: float, **extra) -> dict[str, Any]:
        return {
            "type": "FeatureCollection", "features": features,
            "metadata": self._metadata(len(features), matched, zoom, **extra),
        }
