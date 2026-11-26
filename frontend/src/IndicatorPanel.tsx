import { useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Collapse, Table } from 'antd';
import type { Indicator } from './api';
import { indicatorValue } from './metric';

interface Group {
  id: string;
  number: string;
  name: string;
  groups: Group[];
  indicators: Indicator[];
}
interface Props { items: Indicator[]; selectedId?: string; onSelect: (item: Indicator) => void; mobile: boolean }

function groupedIndicators(items: Indicator[]) {
  const root: Group={id:'root',name:'',number:'',groups:[],indicators:[]};
  const groups=new Map<string,Group>();
  for (const indicator of items) {
    let parent=root;
    for (const group of indicator.groups ?? []) {
      let child=groups.get(group.number);
      if (!child) {
        child={id:group.number,...group,groups:[],indicators:[]};
        groups.set(group.number,child);
        parent.groups.push(child);
      }
      parent=child;
    }
    parent.indicators.push(indicator);
  }
  return {root,keys:[...groups.keys()]};
}

export default function IndicatorPanel({items,selectedId,onSelect,mobile}: Props) {
  const {root,keys}=useMemo(() => groupedIndicators(items),[items]);
  const [open,setOpen]=useState<string[]>();
  const active=open ?? keys;
  const table=(indicators: Indicator[]) => <Table<Indicator> size="small" rowKey="id" pagination={false}
    dataSource={indicators} scroll={mobile ? {x:500} : undefined}
    columns={[
      {title:'№ показателя',dataIndex:'number',width:76},
      {title:'Название',dataIndex:'name'},
      {title:'Единица измерения',dataIndex:'unit',width:82},
      {title:'Значение',width:80,align:'right',render:(_,item)=>indicatorValue(item)},
    ]}
    rowClassName={item=>item.id===selectedId ? 'ant-table-row-selected' : ''}
    onRow={item=>({
      onClick:()=>onSelect(item),
      onKeyDown:event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();onSelect(item);}},
      tabIndex:item.map?.available ? 0 : -1,
      'aria-selected':item.id===selectedId,
      style:{cursor:item.map?.available ? 'pointer' : 'default'},
    })} />;
  const sections=(groups: Group[]): ReactNode => <Collapse size="small" bordered={false}
    activeKey={active} onChange={selected=>{
      const siblingIds=new Set(groups.map(group=>group.id));
      setOpen([...active.filter(id=>!siblingIds.has(id)),...(Array.isArray(selected)?selected:[selected])]);
    }} items={groups.map(group=>({key:group.id,label:`${group.number}. ${group.name}`,
      children:<>{group.indicators.length>0 && table(group.indicators)}{group.groups.length>0 && sections(group.groups)}</>,
    }))} />;
  return <div className="indicator-sections">
    {root.indicators.length>0 && table(root.indicators)}
    {sections(root.groups)}
  </div>;
}
