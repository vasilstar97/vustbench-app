import type { Indicator } from './api';

export function indicatorValue(indicator: Indicator): string {
  return indicator.value === null ? '—' : new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: indicator.precision,
    maximumFractionDigits: indicator.precision,
  }).format(indicator.value);
}

export const weightValue = (value: number) => new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 3}).format(value);

export function metricTitle(metric: Indicator): string {
  const name = metric.number && metric.name.startsWith(metric.number)
    ? metric.name.slice(metric.number.length).replace(/^[\s·:—–-]+/u, '') : metric.name;
  const title = name.replace(/[—–]/gu, '-');
  return title.charAt(0).toLocaleUpperCase('ru-RU') + title.slice(1);
}
