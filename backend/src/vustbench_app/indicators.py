"""File-defined indicators and their saved results, independent of the UI."""

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from vustbench_core.datasets import fingerprint

from .datasets import DatasetStore
from .runs import RunStore
from .settings import Settings


IDENTIFIER = re.compile(r"^[a-z][a-z0-9_:-]*$")
LAYERS = {"buildings", "blocks", "services"}


class IndicatorStore:
    def __init__(self, settings: Settings, datasets: DatasetStore, runs: RunStore):
        self.settings, self.datasets, self.runs = settings, datasets, runs
        self._base_hash = None
        self._input_signature = None
        self._versions: dict[str, str] = {}
        self._graph_signature = None
        self._graph_hash = None

    def definitions(self) -> list[dict[str, Any]]:
        path = self.settings.data_dir / "indicators.json"
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        if config.get("version") != 1 or not isinstance(config.get("indicators"), list):
            raise ValueError("indicators.json requires version=1 and an indicators list")
        result = []
        for item in config["indicators"]:
            if not isinstance(item, dict) or not IDENTIFIER.fullmatch(item.get("id", "")):
                raise ValueError("Invalid indicator identifier")
            if not all(isinstance(item.get(key), str) and item[key] for key in ("name", "calculator", "result_field")):
                raise ValueError(f"Incomplete indicator definition: {item['id']}")
            scale = float(item.get("scale", 1))
            if not math.isfinite(scale):
                raise ValueError("Indicator scale must be finite")
            view = item.get("map")
            if view is not None:
                layers = {view.get("layer"), view.get("overview_layer", view.get("layer")), *view.get("overlays", [])}
                if not layers <= LAYERS or not isinstance(view.get("field"), str):
                    raise ValueError("Invalid indicator map definition")
                domain, colors = view.get("range", [0, 1]), view.get("colors", [])
                if len(domain) != 2 or not all(math.isfinite(float(x)) for x in domain) or domain[0] >= domain[1] or len(colors) < 2:
                    raise ValueError("Indicator maps require an increasing range and at least two colors")
            if item.get("for_each") == "available_service_types":
                for service in self.datasets.catalog():
                    if service.get("available") and service.get("service_count", 0) > 0:
                        result.append({**item, "id": f"{item['id']}:{service['name']}", "name": item["name"].format(**service), "service": service["name"]})
            elif item.get("for_each") is not None:
                raise ValueError("Unknown indicator expansion")
            else:
                result.append(dict(item))
        ids = [item["id"] for item in result]
        if len(ids) != len(set(ids)):
            raise ValueError("Indicator identifiers must be unique")
        return result

    def _data_version(self, service: str) -> str:
        paths = [self.settings.data_dir / name for name in ("buildings.geojson", "blocks.geojson", "bn_service_types.json")]
        signature = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in paths)
        if signature != self._input_signature:
            digest = hashlib.sha256()
            for path in paths:
                digest.update(path.name.encode("utf8"))
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
            self._base_hash, self._input_signature, self._versions = digest, signature, {}
        path = self.settings.data_dir / "services" / f"{service}.geojson"
        stamp = f"{service}:{path.stat().st_mtime_ns}:{path.stat().st_size}"
        if stamp not in self._versions:
            digest = self._base_hash.copy()
            digest.update(path.name.encode("utf8"))
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            self._versions[stamp] = digest.hexdigest()
        return self._versions[stamp]

    def _graph_version(self) -> str | None:
        path = self.settings.graph_path
        if path is None:
            return None
        signature = (path, path.stat().st_mtime_ns, path.stat().st_size)
        if signature != self._graph_signature:
            self._graph_hash, self._graph_signature = fingerprint([path]), signature
        return self._graph_hash

    def latest(self, definition: dict[str, Any], runs: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        service = definition.get("service")
        if not service or definition["calculator"] != "service_provision":
            return None
        if not (self.settings.data_dir / "services" / f"{service}.geojson").is_file():
            return None
        expected, graph = self._data_version(service), self._graph_version()
        for run in runs if runs is not None else self.runs.list(service):
            if run.get("status") != "completed" or run.get("service") != service or run.get("mode") != "walk":
                continue
            if run.get("data_version") != expected or (graph and run.get("graph", {}).get("sha256") != graph):
                continue
            directory = self.runs.directory(run["id"])
            if all((directory / f"{layer}.parquet").is_file() for layer in ("buildings", "blocks", "services")):
                return run
        return None

    def resolve(self, definition: dict[str, Any], runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        run = self.latest(definition, runs)
        value = None
        status = "missing"
        if run:
            value = run.get("summary", {}).get(definition["result_field"])
            status = "ready"
        elif definition["calculator"] == "city_statistic":
            value = self.datasets.city_metadata().get(definition["result_field"])
            status = "ready" if isinstance(value, (int, float)) else "missing"
        elif definition["calculator"] != "service_provision":
            status = "unavailable"
        if isinstance(value, (int, float)) and math.isfinite(value):
            value = value * float(definition.get("scale", 1))
        else:
            value = None
        view = definition.get("map")
        return {
            "id": definition["id"], "number": definition.get("number", ""),
            "groups": definition.get("groups", []),
            "name": definition["name"], "value": value,
            "unit": definition.get("unit", ""), "precision": definition.get("precision", 1),
            "scale": float(definition.get("scale", 1)),
            "status": status,
            "map": {**view, "available": status == "ready"} if view else None,
            "run_id": run["id"] if run else None,
        }

    def rows(self) -> list[dict[str, Any]]:
        runs = self.runs.list()
        return [self.resolve(item, runs) for item in self.definitions()]

    def selected(self, identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
        definition = next((item for item in self.definitions() if item["id"] == identifier), None)
        if definition is None:
            raise KeyError(identifier)
        return definition, self.resolve(definition)
