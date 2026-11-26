import type { FeatureCollection, Geometry } from 'geojson';

export interface City {
  name: string;
  population: number;
  building_count: number;
  blocks_count: number;
  bounds: [number, number, number, number];
  data_version: string;
}

export interface ServiceUnit {
  capacity: number;
  site_area?: number;
  build_floor_area?: number;
}

export interface ServiceType {
  name: string;
  name_ru: string;
  demand: number;
  accessibility: number;
  service_count: number;
  total_capacity: number;
  estimated_capacity_count: number;
  fallback_capacity: number;
  units: ServiceUnit[];
  available?: boolean;
  error?: string | null;
}

export interface RunSummary {
  population: number;
  total_demand: number;
  total_capacity: number;
  supplied_within: number;
  provision_ratio: number;
  deficit_within: number;
  unassigned_demand: number;
  estimated_capacity_count: number;
}

export interface Run {
  id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  service: string;
  mode: string;
  created_at: string;
  updated_at: string;
  message?: string;
  summary?: RunSummary | null;
  parameters?: Record<string, unknown>;
}

export type LayerName = 'buildings' | 'blocks' | 'services';
export interface IndicatorMap {
  available: boolean;
  layer: LayerName;
  overview_layer?: LayerName;
  overlays?: LayerName[];
  field: string;
  range: [number, number];
  colors: string[];
}

export interface Indicator {
  id: string;
  number: string;
  groups: { number: string; name: string }[];
  name: string;
  value: number | null;
  unit: string;
  precision: number;
  scale: number;
  status: 'ready' | 'partial' | 'missing' | 'unavailable';
  source?: 'criteria';
  dependencies?: {id: string; weight?: number}[];
  operation?: 'service' | 'unavailable' | 'weighted_mean' | 'k_sd';
  direction?: 'higher_is_better' | 'lower_is_better';
  coverage?: number;
  population_coverage?: number;
  todo?: string | null;
  service?: string;
  map: IndicatorMap | null;
  run_id: string | null;
}
export type LayerData = FeatureCollection<Geometry, Record<string, unknown>> & {
  metadata?: { truncated?: boolean };
};

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, init);
  if (!response.ok) {
    let detail = `Ошибка запроса (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: unknown; message?: unknown };
      const value = body.detail ?? body.message;
      if (typeof value === 'string') detail = value;
    } catch {
      // Preserve a useful status when the upstream is unavailable or returns HTML.
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  city: (signal?: AbortSignal) => request<City>('/city', { signal }),
  indicators: (signal?: AbortSignal) => request<{items: Indicator[]}>('/indicators', {signal}),
  criteria: (signal?: AbortSignal) => request<{items: Indicator[]; roots: string[]}>('/criteria', {signal}),
  criterionLayer: (id: string, params: URLSearchParams, signal: AbortSignal) =>
    request<LayerData>(`/criteria/${encodeURIComponent(id)}/layers/blocks?${params}`, {signal}),
  indicatorLayer: (id: string, layer: LayerName, params: URLSearchParams, signal: AbortSignal) =>
    request<LayerData>(`/indicators/${encodeURIComponent(id)}/layers/${layer}?${params}`, {signal}),
  serviceTypes: (signal?: AbortSignal) =>
    request<{ items: ServiceType[] }>('/service-types', { signal }),
  runs: (service: string, signal?: AbortSignal) =>
    request<{ items: Run[] }>(`/runs?service=${encodeURIComponent(service)}`, { signal }),
  run: (id: string, signal?: AbortSignal) => request<Run>(`/runs/${encodeURIComponent(id)}`, { signal }),
  createRun: (service: string) => request<Run>('/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service, mode: 'walk' }),
  }),
  layer: (layer: LayerName, params: URLSearchParams, signal: AbortSignal) =>
    request<LayerData>(`/layers/${layer}?${params}`, { signal }),
};

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Не удалось выполнить запрос';
}

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError';
}
