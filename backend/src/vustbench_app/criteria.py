"""Prepare/cache the configured social support graph; expose its dependencies."""

import hashlib
import json
from pathlib import Path
from threading import RLock
from typing import Any

import pandas as pd

from vustbench_core import social
from vustbench_core.aggregation import building_blocks

from .datasets import DatasetStore
from .indicators import IDENTIFIER, IndicatorStore
from .runs import RunStore
from .settings import Settings


class CriteriaStore:
    def __init__(self, settings: Settings, datasets: DatasetStore, indicators: IndicatorStore, runs: RunStore):
        self.settings, self.datasets, self.indicators, self.runs = settings, datasets, indicators, runs
        self._lock = RLock()
        self._version = None
        self._payload: dict[str, Any] = {"items": [], "roots": []}
        self._values = pd.DataFrame()
        self._coverage = pd.DataFrame()
        self._population = pd.Series(dtype=float)

    def service_definitions(self) -> list[dict[str, Any]]:
        path = self.settings.data_dir / "social_support.json"
        if not path.is_file():
            return []
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        return [{"calculator": "service_provision", "service": n["service"], "name": n["name"]}
                for n in config["nodes"] if n["operation"] == "service"]

    def _ensure(self):
        path = self.settings.data_dir / "social_support.json"
        if not path.is_file():
            self._payload = {"items": [], "roots": []}
            return
        content = path.read_bytes()
        config = json.loads(content.decode("utf-8-sig"))
        if config.get("version") != 1 or config.get("interpretation") != "support_higher_is_better":
            raise ValueError("Unsupported social support configuration")
        nodes = config["nodes"]
        for node in nodes:
            if not IDENTIFIER.fullmatch(node["id"]):
                raise ValueError("Invalid social support identifier")
        if not set(config["roots"]) <= {node["id"] for node in nodes}:
            raise ValueError("Unknown social support root")
        runs = self.runs.list()
        sources = {}
        for node in nodes:
            if node["operation"] == "service":
                name = node["service"]
                sources[name] = self.indicators.latest({"service": name, "calculator": "service_provision"}, runs)
        stamps = []
        for filename in ("buildings.geojson", "blocks.geojson", "bn_service_types.json"):
            stat = (self.settings.data_dir / filename).stat()
            stamps.append((filename, stat.st_mtime_ns, stat.st_size))
        for name, run in sorted(sources.items()):
            if run:
                stat = (self.runs.directory(run["id"]) / "blocks.parquet").stat()
                stamps.append((name, run["id"], stat.st_mtime_ns, stat.st_size))
            else:
                stamps.append((name, None))
        digest = hashlib.sha256(content)
        digest.update(Path(social.__file__).read_bytes())
        digest.update(Path(__file__).read_bytes())
        digest.update(json.dumps(stamps).encode())
        version = digest.hexdigest()
        if version == self._version:
            return
        city = self.datasets.city
        directory = self.settings.artifacts_dir / "criteria" / version
        manifest = directory / "summary.json"
        if manifest.is_file() and (directory / "values.parquet").is_file():
            saved = pd.read_parquet(directory / "values.parquet")
            values = saved[[f"value:{node['id']}" for node in nodes]].copy()
            values.columns = [node["id"] for node in nodes]
            coverage = saved[[f"coverage:{node['id']}" for node in nodes]].copy()
            coverage.columns = values.columns
            population = saved["population"]
            payload = json.loads(manifest.read_text(encoding="utf8"))
        else:
            membership = building_blocks(city, self.settings.artifacts_dir)
            population = city.buildings.population.groupby(membership).sum().reindex(city.blocks.index).fillna(0)
            inputs = {}
            service_totals = {}
            for name, run in sources.items():
                if run:
                    inputs[name] = pd.read_parquet(self.runs.directory(run["id"]) / "blocks.parquet", columns=["provision_value"])["provision_value"]
                    # Native run summaries contain exact sums over source buildings,
                    # including those outside the block layer, as in /api/indicators.
                    totals = run.get("summary", {})
                    if "total_demand" in totals and "supplied_within" in totals:
                        service_totals[name] = (totals["total_demand"], totals["supplied_within"])
            result = social.evaluate_support(nodes, inputs, city.blocks.index)
            values, coverage = result.values, result.coverage
            summary = social.summarize_support(nodes, service_totals)
            standard = {"unit": "б.р.", "precision": 3, "scale": 1, "source": "criteria", "groups": []}
            items = []
            for node in nodes:
                identifier = node["id"]
                item = {**standard, **summary[identifier], "id": identifier, "name": node["name"],
                        "number": node.get("symbol", "K_SD" if identifier in config["roots"] else ""),
                        "direction": node.get("direction", "higher_is_better"),
                        "operation": node["operation"], "dependencies": node.get("dependencies", []),
                        "todo": node.get("todo"), "run_id": version}
                available = bool(values[identifier].notna().any())
                item["population_coverage"] = float(population.where(values[identifier].notna(), 0).sum() / population.sum()) if population.sum() else 0
                item["status"] = ("ready" if item["coverage"] >= 1 - 1e-9 else "partial") if item["value"] is not None else "missing"
                item["map"] = {"available": available, "layer": "blocks", "overview_layer": "blocks",
                               "field": "support_value", "range": [0, 1], "colors": ["#ff4d4f", "#ffc53d", "#52c41a"]}
                if node["operation"] == "service":
                    item["service"] = node["service"]
                    item["source_run_id"] = sources[node["service"]]["id"] if sources.get(node["service"]) else None
                items.append(item)
            total_population = float(city.buildings.population.sum())
            payload = {"items": items, "roots": config["roots"], "version": version,
                       "population_in_blocks": float(population.sum()),
                       "population_in_blocks_ratio": float(population.sum() / total_population) if total_population else 0,
                       "interpretation": config["interpretation"], "model_version": config["model_version"],
                       "aggregation": "sum_demand_then_evaluate"}
            directory.mkdir(parents=True, exist_ok=True)
            saved = pd.concat([values.add_prefix("value:"), coverage.add_prefix("coverage:")], axis=1)
            saved["population"] = population
            temporary = directory / "values.tmp.parquet"
            saved.to_parquet(temporary)
            temporary.replace(directory / "values.parquet")
            temporary_json = directory / "summary.tmp.json"
            temporary_json.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), encoding="utf8")
            temporary_json.replace(manifest)
        self._values, self._coverage, self._population = values, coverage, population
        self._payload, self._version = payload, version

    def rows(self) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            return self._payload

    def layer(self, identifier: str, bbox, zoom: float) -> dict[str, Any]:
        with self._lock:
            self._ensure()
            if identifier not in self._values:
                raise KeyError(identifier)
            frame = self.datasets.city.blocks[["id", "geometry"]].copy()
            frame["population"] = self._population
            frame["support_value"] = self._values[identifier]
            frame["data_coverage"] = self._coverage[identifier]
        return self.datasets.frame_collection(frame, "blocks", bbox, zoom, "support_value", ["data_coverage"])
