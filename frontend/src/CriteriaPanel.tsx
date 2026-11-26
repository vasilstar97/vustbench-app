import { useMemo, useState } from 'react';
import { Breadcrumb, Button, Card, Empty, Progress, Space, Tag, Typography } from 'antd';
import { ArrowLeftOutlined, ArrowRightOutlined, EyeOutlined } from '@ant-design/icons';
import type { Indicator } from './api';
import { indicatorValue, metricTitle, weightValue } from './metric';
import SupportProfile from './SupportProfile';

interface Props {
  items: Indicator[];
  roots: string[];
  selectedId?: string;
  onSelect: (item: Indicator) => void;
  onNavigate: () => void;
}

function supportColor(value: number, direction?: Indicator['direction']): string {
  const score = direction === 'lower_is_better' ? 1-value : value;
  const colors = [[255,77,79],[255,197,61],[82,196,26]];
  const segment = score < .5 ? 0 : 1;
  const factor = Math.min(1,Math.max(0,score*2-segment));
  return `rgb(${colors[segment]!.map((channel,index) => Math.round(channel + (colors[segment+1]![index]!-channel)*factor)).join(',')})`;
}

export default function CriteriaPanel({items,roots,selectedId,onSelect,onNavigate}: Props) {
  const [path,setPath] = useState<string[]>([]);
  const lookup = useMemo(() => new Map(items.map(item => [item.id,item])),[items]);
  const current = lookup.get(path[path.length-1] ?? '');
  const entries = current?.dependencies ?? roots.map(id => ({id,weight:undefined}));
  const navigate = (next: string[]) => {setPath(next); onNavigate();};
  const dimensions = current?.operation === 'k_sd' ? (current.dependencies ?? []).map(edge => lookup.get(edge.id)!).filter(Boolean) : [];
  return <Space orientation="vertical" size="middle" className="criteria-panel">
    {path.length > 0 && <div className="criteria-navigation">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(path.slice(0,-1))}>Назад</Button>
      <Breadcrumb items={[
        {title:<Button type="link" size="small" onClick={() => navigate([])}>Критерии</Button>},
        ...path.map((id,index) => ({title: index === path.length-1
          ? <Typography.Text>{lookup.has(id) ? metricTitle(lookup.get(id)!) : ''}</Typography.Text>
          : <Button type="link" size="small" onClick={() => navigate(path.slice(0,index+1))}>{lookup.has(id) ? metricTitle(lookup.get(id)!) : ''}</Button>})),
      ]} />
    </div>}
    {current && <div className="criterion-current">
      <Typography.Title level={5}>{metricTitle(current)}</Typography.Title>
      <Space wrap><Typography.Text strong>{indicatorValue(current)} {current.unit}</Typography.Text>
        <Button size="small" icon={<EyeOutlined />} disabled={!current.map?.available} onClick={() => onSelect(current)}>Показать слой</Button>
      </Space>
    </div>}
    {dimensions.length === 4 && <SupportProfile dimensions={dimensions} name={metricTitle(current!)} />}
    {!entries.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Нет зависимых показателей" />}
    {entries.map(edge => {
      const metric=lookup.get(edge.id);
      if (!metric) return null;
      const weighted=current?.operation==='weighted_mean' && edge.weight !== undefined;
      return <Card size="small" key={metric.id} className="criterion-card"
        title={<Typography.Text strong className="criterion-title">{metricTitle(metric)}</Typography.Text>}
        extra={selectedId===metric.id ? <Tag color="blue">На карте</Tag> : undefined}>
        <Space orientation="vertical" size="small" className="full-width">
          <Space wrap size="middle">
            <Typography.Text strong>Значение: {indicatorValue(metric)} {metric.unit}</Typography.Text>
            {weighted && <Typography.Text type="secondary">Вес: {weightValue(edge.weight!)}</Typography.Text>}
          </Space>
          <Progress percent={metric.value === null ? 0 : metric.value*100} showInfo={false} size="small"
            strokeColor={metric.value === null ? '#bfbfbf' : supportColor(metric.value,metric.direction)}
            aria-label={`${metricTitle(metric)}: ${indicatorValue(metric)}`} />
          <Space wrap>
            <Button size="small" icon={<EyeOutlined />} disabled={!metric.map?.available}
              type={selectedId===metric.id ? 'primary' : 'default'} onClick={() => onSelect(metric)}>Показать слой</Button>
            <Button size="small" icon={<ArrowRightOutlined />} disabled={!metric.dependencies?.length}
              onClick={() => navigate([...path,metric.id])}>Подробнее</Button>
          </Space>
        </Space>
      </Card>;
    })}
  </Space>;
}
