"""Application configuration; computation parameters belong to the core."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    artifacts_dir: Path
    graph_path: Path | None = None
    osm_id: int | None = None
    max_layer_features: int = 12_000
    graph_timeout_seconds: int = 600
    run_timeout_seconds: int = 1800

    @classmethod
    def from_env(cls) -> "Settings":
        graph_path = os.environ.get("VUSTBENCH_GRAPH_PATH")
        osm_id = os.environ.get("VUSTBENCH_OSM_ID")
        data_dir = Path(os.environ.get("VUSTBENCH_DATA_DIR", "data")).resolve()
        if not graph_path:
            candidates = list(data_dir.glob("*.urbangraph"))
            preferred = data_dir / "walk.urbangraph"
            graph_path = str(preferred) if preferred.is_file() else str(candidates[0]) if len(candidates) == 1 else None
        return cls(
            data_dir=data_dir,
            artifacts_dir=Path(os.environ.get("VUSTBENCH_ARTIFACTS_DIR", "artifacts")).resolve(),
            graph_path=Path(graph_path).resolve() if graph_path else None,
            osm_id=int(osm_id) if osm_id else None,
            max_layer_features=int(os.environ.get("VUSTBENCH_MAX_LAYER_FEATURES", "12000")),
            graph_timeout_seconds=int(os.environ.get("VUSTBENCH_GRAPH_TIMEOUT_SECONDS", "600")),
            run_timeout_seconds=int(os.environ.get("VUSTBENCH_RUN_TIMEOUT_SECONDS", "1800")),
        )
