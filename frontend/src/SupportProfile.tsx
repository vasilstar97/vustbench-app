import { useId } from 'react';
import { Card, theme } from 'antd';
import type { Indicator } from './api';
import { indicatorValue } from './metric';

interface Props { dimensions: Indicator[]; name: string }

export default function SupportProfile({dimensions, name}: Props) {
  const {token} = theme.useToken();
  const marker = useId();
  // Formula (35*): work (left), leisure (right), development (top), risk (bottom).
  const axes = [
    {metric: dimensions[2], x: 0, y: -1, label: 'Развитие', lx: 220, ly: 20, anchor: 'middle'},
    {metric: dimensions[1], x: 1, y: 0, label: 'Траты / досуг', lx: 354, ly: 165, anchor: 'start'},
    {metric: dimensions[3], x: 0, y: 1, label: 'Риски', lx: 220, ly: 308, anchor: 'middle'},
    {metric: dimensions[0], x: -1, y: 0, label: 'Доход', lx: 86, ly: 165, anchor: 'end'},
  ];
  const points = axes.map(axis => axis.metric?.value === null || !axis.metric
    ? null : [220 + axis.x * axis.metric.value * 104, 160 + axis.y * axis.metric.value * 104]);
  const complete = points.every(point => point !== null);
  const description = axes.map(axis => `${axis.label}: ${axis.metric ? indicatorValue(axis.metric) : '—'}`).join('; ');
  return <Card size="small" title="Профиль поддержки" className="support-profile">
    <svg viewBox="0 0 440 330" role="img" aria-label={`Профиль ${name}. ${description}`}>
      <defs><marker id={marker} markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6" fill={token.colorTextSecondary} /></marker></defs>
      {[.5, 1].map(radius => <polygon key={radius} points={`220,${160-104*radius} ${220+104*radius},160 220,${160+104*radius} ${220-104*radius},160`}
        fill="none" stroke={token.colorBorderSecondary} strokeDasharray="4 4" />)}
      {axes.map(axis => <g key={axis.label}>
        <line x1="220" y1="160" x2={220+axis.x*122} y2={160+axis.y*122} stroke={token.colorTextSecondary} markerEnd={`url(#${marker})`} />
        <text x={axis.lx} y={axis.ly} textAnchor={axis.anchor as 'start' | 'middle' | 'end'} fill={token.colorText} fontSize="12">{axis.label}</text>
      </g>)}
      <text x="226" y="175" fill={token.colorTextSecondary} fontSize="10">0</text>
      <text x="230" y="57" fill={token.colorTextSecondary} fontSize="10">1</text>
      {complete && <polygon points={points.map(point => point!.join(',')).join(' ')} fill={token.colorErrorBg} stroke={token.colorError} strokeWidth="2" />}
      {!complete && points.map((point,index) => point && points[(index+1)%4] ? <line key={index}
        x1={point[0]} y1={point[1]} x2={points[(index+1)%4]![0]} y2={points[(index+1)%4]![1]}
        stroke={token.colorError} strokeWidth="2" /> : null)}
      {axes.map((axis,index) => <g key={axis.label}>
        {points[index] && <circle cx={points[index]![0]} cy={points[index]![1]} r="4" fill={token.colorError} />}
        <text x={220 + axis.x*78 + (axis.x === 0 ? 16 : 0)} y={160 + axis.y*78 + (axis.y === 0 ? -16 : 0)}
          textAnchor={axis.x === -1 ? 'end' : 'start'} fill={token.colorTextSecondary} fontSize="12">
          {axis.metric ? indicatorValue(axis.metric) : '—'}
        </text>
      </g>)}
      {!complete && <text x="220" y="328" textAnchor="middle" fill={token.colorTextSecondary} fontSize="11">Нет данных</text>}
    </svg>
  </Card>;
}
