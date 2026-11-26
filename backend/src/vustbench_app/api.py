"""FastAPI routes for input layers, run history and background calculations."""

import asyncio
from contextlib import asynccontextmanager, suppress
import logging
import math
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Path, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .datasets import DatasetStore
from .criteria import CriteriaStore
from .indicators import IndicatorStore
from .runs import RUN_ID_PATTERN, RunBusyError, RunManager, RunStore
from .settings import Settings


logger = logging.getLogger(__name__)
RunId = Annotated[str, Path(pattern=RUN_ID_PATTERN.pattern, description="Persistent calculation identifier")]


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = Field(default="school", pattern=r"^[a-z][a-z0-9_]{0,63}$")
    mode: Literal["walk"] = Field(default="walk", description="MVP supports pedestrian accessibility.")


class Run(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    service: str
    mode: str
    created_at: str
    updated_at: str | None = None
    message: str = ""
    summary: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


def parse_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    try:
        parts = tuple(float(number) for number in value.split(","))
    except ValueError as error:
        raise HTTPException(422, "bbox должен содержать четыре числа: minx,miny,maxx,maxy.") from error
    if len(parts) != 4 or not all(math.isfinite(number) for number in parts):
        raise HTTPException(422, "bbox должен содержать четыре конечных числа.")
    minx, miny, maxx, maxy = parts
    if not (-180 <= minx < maxx <= 180 and -90 <= miny < maxy <= 90):
        raise HTTPException(422, "bbox должен быть упорядочен в пределах EPSG:4326.")
    return minx, miny, maxx, maxy


def create_app(settings: Settings | None = None, datasets: DatasetStore | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    runs = RunStore(settings.artifacts_dir)
    manager = RunManager(settings, runs)
    datasets = datasets or DatasetStore(settings, runs)
    indicators = IndicatorStore(settings, datasets, runs)
    criteria = CriteriaStore(settings, datasets, indicators, runs)

    async def monitor() -> None:
        while True:
            await asyncio.sleep(2)
            try:
                await asyncio.to_thread(manager.reap)
            except (OSError, ValueError):
                logger.exception("Could not update calculation process status")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        runs.recover_interrupted()
        task = asyncio.create_task(monitor())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            await asyncio.to_thread(manager.shutdown)

    app = FastAPI(
        title="VUSTBench testing ground",
        version=__version__,
        description=(
            "Исходная модель города и воспроизводимые расчёты обеспеченности. "
            "Объекты в EPSG:4326. Все математические модели выполняет vustbench-core. "
            "API запускает один изолированный процесс расчёта; результаты и статусы сохраняются на диск."
        ),
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.runs = runs
    app.state.manager = manager
    app.state.datasets = datasets
    app.state.indicators = indicators
    app.state.criteria = criteria

    def catalog() -> list[dict[str, Any]]:
        try:
            return datasets.catalog()
        except (FileNotFoundError, ValueError, OSError) as error:
            raise HTTPException(503, "Каталог сервисов недоступен. Проверьте входные данные.") from error

    def check_service(service: str, *, for_run: bool = False) -> dict[str, Any]:
        item = next((item for item in catalog() if item["name"] == service), None)
        if item is None:
            raise HTTPException(422 if for_run else 404, f"Неизвестный тип сервиса: {service}.")
        if item.get("available") is False:
            raise HTTPException(422, item.get("error") or "Для выбранного сервиса недостаточно входных данных.")
        return item

    def read_run(run_id: str) -> dict[str, Any]:
        manager.reap()
        try:
            return runs.read(run_id)
        except FileNotFoundError as error:
            raise HTTPException(404, "Расчёт не найден.") from error
        except (ValueError, OSError) as error:
            raise HTTPException(503, "Не удалось прочитать результат расчёта.") from error

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__, "data_available": settings.data_dir.is_dir()}

    @app.get("/api/city", tags=["inputs"])
    def get_city() -> dict[str, Any]:
        try:
            return datasets.city_metadata()
        except (FileNotFoundError, ValueError, OSError) as error:
            raise HTTPException(503, "Модель города недоступна. Проверьте входные слои.") from error

    @app.get("/api/service-types", tags=["inputs"])
    def get_service_types() -> dict[str, Any]:
        return {"items": catalog()}

    @app.get("/api/indicators", tags=["indicators"])
    def get_indicators() -> dict[str, Any]:
        try:
            return {"items": indicators.rows()}
        except (OSError, ValueError, KeyError) as error:
            raise HTTPException(503, "Не удалось загрузить показатели.") from error

    @app.get("/api/criteria", tags=["criteria"])
    def get_criteria() -> dict[str, Any]:
        try:
            return criteria.rows()
        except (OSError, ValueError, KeyError) as error:
            logger.exception("Could not prepare social support criteria")
            raise HTTPException(503, "Не удалось загрузить критерии.") from error

    @app.get("/api/criteria/{criterion_id}/layers/blocks", tags=["criteria"])
    def criterion_layer(
        criterion_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_:-]*$")],
        bbox: Annotated[str | None, Query()] = None,
        zoom: Annotated[float, Query(ge=0, le=22)] = 12,
    ) -> dict[str, Any]:
        bounds = parse_bbox(bbox)
        try:
            return criteria.layer(criterion_id, bounds, zoom)
        except KeyError as error:
            raise HTTPException(404, "Критерий не найден.") from error
        except (OSError, ValueError) as error:
            logger.exception("Could not prepare criterion layer")
            raise HTTPException(503, "Не удалось загрузить слой критерия.") from error

    @app.get("/api/indicators/{indicator_id}/layers/{layer}", tags=["indicators"])
    def indicator_layer(
        indicator_id: Annotated[str, Path(pattern=r"^[a-z][a-z0-9_:-]*$")],
        layer: Literal["buildings", "blocks", "services"],
        bbox: Annotated[str | None, Query()] = None,
        zoom: Annotated[float, Query(ge=0, le=22)] = 12,
    ) -> dict[str, Any]:
        bounds = parse_bbox(bbox)
        try:
            definition, row = indicators.selected(indicator_id)
        except KeyError as error:
            raise HTTPException(404, "Показатель не найден.") from error
        except (OSError, ValueError) as error:
            raise HTTPException(503, "Не удалось загрузить показатель.") from error
        view = row.get("map")
        if not view or not view["available"]:
            raise HTTPException(409, "Слой показателя пока недоступен.")
        allowed = {view["layer"], view.get("overview_layer", view["layer"]), *view.get("overlays", [])}
        if layer not in allowed:
            raise HTTPException(404, "Показатель не поддерживает этот слой.")
        try:
            return datasets.layer(layer, definition.get("service", ""), row["run_id"], bounds, zoom, value_field=view["field"])
        except (OSError, ValueError) as error:
            raise HTTPException(503, "Не удалось загрузить слой показателя.") from error

    @app.get("/api/layers/{layer}", tags=["layers"])
    def get_layer(
        layer: Literal["buildings", "blocks", "services"],
        bbox: Annotated[str | None, Query(description="EPSG:4326 minx,miny,maxx,maxy")] = None,
        zoom: Annotated[float, Query(ge=0, le=22, description="Buildings are available at zoom >= 12")] = 12,
        service: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = "school",
        run_id: Annotated[str | None, Query(pattern=RUN_ID_PATTERN.pattern)] = None,
    ) -> dict[str, Any]:
        bounds = parse_bbox(bbox)
        if layer == "services" or run_id:
            check_service(service)
        if run_id:
            run = read_run(run_id)
            if run["status"] != "completed":
                raise HTTPException(409, "Слой результатов доступен только после успешного расчёта.")
            if run["service"] != service:
                raise HTTPException(422, "Тип сервиса не совпадает с выбранным расчётом.")
        try:
            result = datasets.layer(layer, service, run_id, bounds, zoom)
        except (FileNotFoundError, ValueError, OSError) as error:
            raise HTTPException(503, "Не удалось прочитать слой. Проверьте данные и файлы расчёта.") from error
        return result

    @app.get("/api/runs", tags=["runs"])
    def get_runs(service: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,63}$")] = None):
        if service:
            check_service(service)
        manager.reap()
        return {"items": runs.list(service)}

    @app.get("/api/runs/{run_id}", response_model=Run, tags=["runs"])
    def get_run(run_id: RunId):
        return read_run(run_id)

    @app.post("/api/runs", response_model=Run, status_code=202, tags=["runs"])
    def create_run(request: CreateRun, response: Response):
        check_service(request.service, for_run=True)
        try:
            run = manager.submit(request.service, request.mode)
        except RunBusyError as error:
            raise HTTPException(409, str(error)) from error
        except OSError as error:
            raise HTTPException(503, "Не удалось запустить процесс расчёта.") from error
        response.headers["Location"] = f"/api/runs/{run['id']}"
        return run

    return app
