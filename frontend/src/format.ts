export const number = (value: unknown, digits = 0): string =>
  typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value)
    : '—';

export const percent = (value: unknown): string =>
  typeof value === 'number' && Number.isFinite(value) ? `${number(value * 100, 1)} %` : '—';

export const dateTime = (value: string): string => {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(date);
};
