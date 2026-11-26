import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Space, Spin, Tag, Typography } from 'antd';
import { AimOutlined, CloseOutlined } from '@ant-design/icons';
import * as maplibregl from 'maplibre-gl';
import mapWorkerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import type { GeoJSONSource, Map as LibreMap } from 'maplibre-gl';
import { api, errorMessage, isAbort } from './api';
import type { City, Indicator, LayerData, LayerName } from './api';
import { number } from './format';
import { metricTitle } from './metric';

maplibregl.setWorkerUrl(mapWorkerUrl);
const empty: LayerData = { type: 'FeatureCollection', features: [] };
const detailZoom = 12;
const areaLayers = ['blocks-fill', 'buildings-fill', 'buildings-points'];

interface Props { city: City; indicator?: Indicator }
interface Selection { kind: LayerName; properties: Record<string, unknown> }

function setData(map: LibreMap, source: string, data: LayerData) {
  (map.getSource(source) as GeoJSONSource | undefined)?.setData(data);
}

function fitCity(map: LibreMap, city: City) {
  const [west, south, east, north] = city.bounds;
  map.fitBounds([[west, south], [east, north]], { padding: 45, duration: 500, maxZoom: 12 });
}

function metric(value: unknown, indicator?: Indicator): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || !indicator) return '—';
  return `${new Intl.NumberFormat('ru-RU', {maximumFractionDigits: indicator.precision}).format(value * indicator.scale)} ${indicator.unit}`.trim();
}

export default function CityMap({ city, indicator }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LibreMap | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string>();
  const [truncated, setTruncated] = useState(false);
  const [selection, setSelection] = useState<Selection>();
  const [retry, setRetry] = useState(0);
  const view = indicator?.map?.available ? indicator.map : null;
  const viewKey = JSON.stringify(view);

  useEffect(() => {
    if (!container.current) return;
    let map: LibreMap;
    try {
      map = new maplibregl.Map({
        container: container.current,
        center: [(city.bounds[0] + city.bounds[2]) / 2, (city.bounds[1] + city.bounds[3]) / 2],
        zoom: 9, minZoom: 7, maxZoom: 18,
        maxBounds: [[26, 57], [35, 63]],
        attributionControl: false,
        style: {
          version: 8,
          sources: {
            osm: {type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256,
              maxzoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>'},
            blocks: {type: 'geojson', data: empty},
            buildings: {type: 'geojson', data: empty},
            services: {type: 'geojson', data: empty},
          },
          layers: [
            {id: 'background', type: 'background', paint: {'background-color': '#f0f2f5'}},
            {id: 'osm', type: 'raster', source: 'osm', paint: {
              'raster-opacity': 0.7, 'raster-saturation': -1, 'raster-contrast': -0.1,
            }},
            {id: 'blocks-fill', type: 'fill', source: 'blocks', maxzoom: detailZoom,
              paint: {'fill-color': '#91caff', 'fill-opacity': 0.65, 'fill-outline-color': '#ffffff'}},
            {id: 'buildings-fill', type: 'fill', source: 'buildings', minzoom: detailZoom,
              paint: {'fill-color': '#4096ff', 'fill-opacity': 0.8}},
            {id: 'buildings-points', type: 'circle', source: 'buildings', minzoom: detailZoom,
              filter: ['==', ['geometry-type'], 'Point'], paint: {'circle-radius': 3, 'circle-color': '#4096ff'}},
            {id: 'services-circles', type: 'circle', source: 'services', paint: {
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 7, 2, 12, 4, 17, 7],
              'circle-color': '#722ed1', 'circle-stroke-width': 1, 'circle-stroke-color': '#ffffff',
            }},
          ],
        },
      });
      mapRef.current = map;
      map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-right');
      map.addControl(new maplibregl.ScaleControl({unit: 'metric'}), 'bottom-left');
      map.addControl(new maplibregl.AttributionControl({compact: true}), 'bottom-right');
      map.on('style.load', () => {fitCity(map, city); setReady(true);});
      map.on('click', event => {
        const feature = map.queryRenderedFeatures(event.point, {layers: ['services-circles', ...areaLayers]})[0];
        if (!feature) {setSelection(undefined); return;}
        if (['services', 'buildings', 'blocks'].includes(feature.source)) {
          setSelection({kind: feature.source as LayerName, properties: feature.properties});
        }
      });
      for (const layer of ['services-circles', ...areaLayers]) {
        map.on('mouseenter', layer, () => {map.getCanvas().style.cursor = 'pointer';});
        map.on('mouseleave', layer, () => {map.getCanvas().style.cursor = '';});
      }
    } catch (reason) {setError(errorMessage(reason)); return;}
    const resize = new ResizeObserver(() => map.resize());
    resize.observe(container.current);
    return () => {resize.disconnect(); map.remove(); mapRef.current = null; setReady(false);};
  }, [city]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    let color: maplibregl.ExpressionSpecification | string = '#91caff';
    if (view) {
      const stops = view.colors.flatMap((color, index) => [
        view.range[0] + (view.range[1] - view.range[0]) * index / (view.colors.length - 1), color,
      ]);
      color = ['case', ['==', ['get', view.field], null], '#bfbfbf',
        ['interpolate', ['linear'], ['to-number', ['get', view.field], 0], ...stops],
      ] as unknown as maplibregl.ExpressionSpecification;
    }
    map.setPaintProperty('blocks-fill', 'fill-color', color);
    map.setPaintProperty('buildings-fill', 'fill-color', color);
    map.setPaintProperty('buildings-points', 'circle-color', color);
    map.setPaintProperty('services-circles', 'circle-color', view?.layer === 'services' ? color : '#722ed1');
    const primary = view?.layer ?? 'buildings';
    const overview = view ? (view.overview_layer ?? view.layer) : 'blocks';
    for (const [layer, source] of [['blocks-fill', 'blocks'], ['buildings-fill', 'buildings'], ['buildings-points', 'buildings']] as const) {
      map.setLayerZoomRange(layer, source === overview ? 0 : detailZoom, source === primary ? 24 : detailZoom);
    }
  }, [ready, viewKey]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    let timer: ReturnType<typeof setTimeout>;
    let controller: AbortController | undefined;
    let requestId = 0;
    setSelection(undefined);
    for (const source of ['blocks', 'buildings', 'services']) setData(map, source, empty);
    const load = async () => {
      controller?.abort();
      controller = new AbortController();
      const current = ++requestId;
      const bounds = map.getBounds();
      const zoom = map.getZoom();
      const layer: LayerName = zoom >= detailZoom ? (view?.layer ?? 'buildings') : (view ? (view.overview_layer ?? view.layer) : 'blocks');
      const params = new URLSearchParams({
        bbox: [Math.max(-180, bounds.getWest()), Math.max(-85, bounds.getSouth()), Math.min(180, bounds.getEast()), Math.min(85, bounds.getNorth())].map(v => v.toFixed(6)).join(','),
        zoom: zoom.toFixed(2),
      });
      setLoading(true);
      try {
        const names: LayerName[] = [...new Set([layer, ...(view?.overlays ?? [])])];
        const responses = await Promise.all(names.map(name => view && indicator
          ? (indicator.source === 'criteria'
            ? api.criterionLayer(indicator.id, params, controller!.signal)
            : api.indicatorLayer(indicator.id, name, params, controller!.signal))
          : api.layer(name, params, controller!.signal)));
        if (controller.signal.aborted || current !== requestId) return;
        for (const name of ['blocks', 'buildings', 'services'] as LayerName[]) {
          const index = names.indexOf(name);
          setData(map, name, index >= 0 ? (responses[index] ?? empty) : empty);
        }
        setTruncated(responses.some(response => response.metadata?.truncated));
        setError(undefined);
      } catch (reason) {if (!isAbort(reason) && current === requestId) setError(errorMessage(reason));}
      finally {if (current === requestId) setLoading(false);}
    };
    const schedule = () => {
      clearTimeout(timer); controller?.abort(); ++requestId;
      timer = setTimeout(() => {void load();}, 200);
    };
    map.on('moveend', schedule); schedule();
    return () => {clearTimeout(timer); controller?.abort(); ++requestId; map.off('moveend', schedule);};
  }, [ready, indicator?.id, indicator?.run_id, viewKey, retry]);

  const properties = selection?.properties;
  return <div className="city-map">
    <div ref={container} className="map-canvas" role="region" aria-label="Карта показателей города" />
    <div className="map-top-left">
      {indicator && view && <Card size="small">
        <Space orientation="vertical" size="small">
          <Typography.Text strong>{metricTitle(indicator)}</Typography.Text>
          <Space>{view.colors.map((color, index) => <Tag color={color} key={color}>
            {metric(view.range[0] + (view.range[1] - view.range[0]) * index / (view.colors.length - 1), indicator)}
          </Tag>)}{indicator.source === 'criteria' && <Tag>Нет данных</Tag>}{loading && <Spin size="small" />}</Space>
        </Space>
      </Card>}
      {truncated && <Alert type="info" title="Приблизьте карту, чтобы увидеть все объекты." />}
      {error && <Alert type="error" title="Не удалось загрузить карту" description={error}
        action={<Button size="small" onClick={() => setRetry(value => value + 1)}>Повторить</Button>} />}
    </div>
    <Button className="map-home" icon={<AimOutlined />} aria-label="Показать весь город"
      onClick={() => {if (mapRef.current) fitCity(mapRef.current, city);}} />
    {selection && properties && <Card size="small" className="map-selection"
      title={selection.kind === 'services' ? 'Сервис' : selection.kind === 'blocks' ? 'Квартал' : 'Здание'}
      extra={<Button type="text" size="small" icon={<CloseOutlined />} aria-label="Закрыть карточку" onClick={() => setSelection(undefined)} />}>
      <Descriptions size="small" column={1} items={[
        ...(indicator && view && (selection.kind === view.layer || selection.kind === view.overview_layer)
          ? [{key: 'value', label: metricTitle(indicator), children: metric(properties[view.field], indicator)}] : []),
        ...(typeof properties.population === 'number' ? [{key: 'population', label: 'Население', children: number(properties.population)}] : []),
        ...(selection.kind === 'services' ? [
          {key: 'capacity', label: 'Вместимость', children: number(properties.capacity)},
          {key: 'load', label: 'Загрузка', children: number(properties.service_load)},
          ...(properties.capacity_estimated ? [{key: 'estimated', label: 'Вместимость', children: 'Оценённая'}] : []),
        ] : []),
      ]} />
    </Card>}
  </div>;
}
