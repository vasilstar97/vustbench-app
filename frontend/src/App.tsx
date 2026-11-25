import { useEffect, useState } from 'react';
import { Alert, Button, Drawer, Grid, Layout, Space, Spin, Table, Typography } from 'antd';
import { useMemo } from 'react';
import type { Key } from 'react';
import { BarChartOutlined } from '@ant-design/icons';
import CityMap from './CityMap';
import { api, errorMessage, isAbort } from './api';
import type { City, Indicator } from './api';

interface IndicatorGroup {
  id: string;
  number: string;
  name: string;
  children: IndicatorRow[];
}
type IndicatorRow = Indicator | IndicatorGroup;

function groupIndicators(indicators: Indicator[]): IndicatorRow[] {
  const roots: IndicatorRow[] = [];
  const groups = new Map<string, IndicatorGroup>();
  for (const indicator of indicators) {
    let children = roots;
    for (const group of indicator.groups ?? []) {
      let parent = groups.get(group.number);
      if (!parent) {
        parent = { ...group, id: `group:${group.number}`, children: [] };
        groups.set(group.number, parent);
        children.push(parent);
      }
      children = parent.children;
    }
    children.push(indicator);
  }
  return roots;
}

export function indicatorValue(indicator: Indicator): string {
  if (indicator.value === null) return '—';
  const value = new Intl.NumberFormat('ru-RU', {
    minimumFractionDigits: indicator.precision,
    maximumFractionDigits: indicator.precision,
  }).format(indicator.value);
  return value;
}

export default function App() {
  const screens = Grid.useBreakpoint();
  const mobile = !screens.lg;
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [city, setCity] = useState<City>();
  const [indicators, setIndicators] = useState<Indicator[]>([]);
  const [expandedKeys, setExpandedKeys] = useState<readonly Key[]>([]);
  const groupedIndicators = useMemo(() => groupIndicators(indicators), [indicators]);
  const [selectedId, setSelectedId] = useState<string>();
  const [error, setError] = useState<string>();
  const [retry, setRetry] = useState(0);
  const selected = indicators.find(item => item.id === selectedId);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let hasCity = false;
    const update = async () => {
      try {
        let result;
        if (!hasCity) {
          const [cityData, indicatorData] = await Promise.all([api.city(controller.signal), api.indicators(controller.signal)]);
          if (controller.signal.aborted) return;
          setCity(cityData);
          hasCity = true;
          result = indicatorData;
        } else {
          result = await api.indicators(controller.signal);
        }
        const { items } = result;
        if (controller.signal.aborted) return;
        setIndicators(items);
        setSelectedId(previous => {
          if (items.some(item => item.id === previous && item.map?.available)) return previous;
          return items.find(item => item.map?.available)?.id;
        });
        setError(undefined);
      } catch (reason) {
        if (!isAbort(reason)) setError(errorMessage(reason));
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(update, 5000);
      }
    };
    void update();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [retry]);

  const choose = (indicator: Indicator) => {
    if (!indicator.map?.available) return;
    setSelectedId(indicator.id);
    setDrawerOpen(false);
  };

  const panel = <div className="sidebar-content">
    <Typography.Title level={4} className="no-top-margin">Показатели</Typography.Title>
    <Table<IndicatorRow>
      size="small"
      rowKey="id"
      pagination={false}
      dataSource={groupedIndicators}
      expandable={{
        expandedRowKeys: expandedKeys,
        onExpandedRowsChange: setExpandedKeys,
        expandRowByClick: true,
        expandIconColumnIndex: 1,
        indentSize: 12,
      }}
      loading={!indicators.length && !error}
      locale={{ emptyText: 'Нет показателей' }}
      columns={[
        { title: '№ показателя', dataIndex: 'number', key: 'number', width: 84 },
        { title: 'Название', dataIndex: 'name', key: 'name', render: (name, item) =>
          'children' in item ? <Typography.Text strong>{name}</Typography.Text> : name },
        { title: 'Единица измерения', dataIndex: 'unit', key: 'unit', width: 94 },
        { title: 'Значение', key: 'value', align: 'right', width: 86,
          render: (_, item) => 'children' in item ? null : indicatorValue(item) },
      ]}
      rowClassName={item => item.id === selectedId ? 'ant-table-row-selected' : ''}
      onRow={item => 'children' in item ? {} : ({
        onClick: () => choose(item),
        onKeyDown: event => {
          if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose(item); }
        },
        tabIndex: item.map?.available ? 0 : -1,
        'aria-selected': item.id === selectedId,
        style: { cursor: item.map?.available ? 'pointer' : 'default' },
      })}
    />
  </div>;

  return <Layout className="app-layout">
    <Layout.Header className="app-header">
      <Space size="middle">
        <Typography.Title level={4} className="brand">VUST Bench</Typography.Title>
        {city && <Typography.Text>{city.name}</Typography.Text>}
      </Space>
      {mobile && <Button icon={<BarChartOutlined />} onClick={() => setDrawerOpen(true)}>Показатели</Button>}
    </Layout.Header>
    {error && <Alert type="error" title={error}
      action={<Button size="small" onClick={() => setRetry(value => value + 1)}>Повторить</Button>} />}
    {city ? <Layout className="app-body">
      <Layout.Content className="map-content"><CityMap city={city} indicator={selected} /></Layout.Content>
      {!mobile && <Layout.Sider width={560} theme="light" className="app-sidebar">{panel}</Layout.Sider>}
      <Drawer title="Показатели" placement="right" open={mobile && drawerOpen}
        onClose={() => setDrawerOpen(false)} size={560} className="mobile-sidebar">{panel}</Drawer>
    </Layout> : <Layout.Content className="initial-state"><Spin /></Layout.Content>}
  </Layout>;
}
