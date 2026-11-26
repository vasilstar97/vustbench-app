import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Drawer, Grid, Layout, Space, Spin, Tabs, Typography } from 'antd';
import { BarChartOutlined } from '@ant-design/icons';
import CityMap from './CityMap';
import CriteriaPanel from './CriteriaPanel';
import IndicatorPanel from './IndicatorPanel';
import { api, errorMessage, isAbort } from './api';
import type { City, Indicator } from './api';

export default function App() {
  const screens=Grid.useBreakpoint();
  const mobile=!screens.lg;
  const [drawerOpen,setDrawerOpen]=useState(false);
  const [city,setCity]=useState<City>();
  const [indicators,setIndicators]=useState<Indicator[]>([]);
  const [criteria,setCriteria]=useState<Indicator[]>([]);
  const [criteriaRoots,setCriteriaRoots]=useState<string[]>([]);
  const [activeTab,setActiveTab]=useState('criteria');
  const [selectedId,setSelectedId]=useState<string>();
  const [error,setError]=useState<string>();
  const [retry,setRetry]=useState(0);
  const scrollBody=useRef<HTMLDivElement>(null);
  const selected=[...criteria,...indicators].find(item=>item.id===selectedId);

  useEffect(()=>{
    const controller=new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let hasCity=false;
    const update=async()=>{
      try {
        let result;
        let criterionData;
        if(!hasCity) {
          const [cityData,indicatorData,criterionResult]=await Promise.all([
            api.city(controller.signal),api.indicators(controller.signal),api.criteria(controller.signal),
          ]);
          if(controller.signal.aborted)return;
          setCity(cityData);
          hasCity=true;
          result=indicatorData;
          criterionData=criterionResult;
        } else {
          [result,criterionData]=await Promise.all([api.indicators(controller.signal),api.criteria(controller.signal)]);
        }
        if(controller.signal.aborted)return;
        setIndicators(result.items);
        setCriteria(criterionData.items);
        setCriteriaRoots(criterionData.roots);
        setSelectedId(previous=>{
          const candidates=[...criterionData.items,...result.items];
          if(candidates.some(item=>item.id===previous && item.map?.available))return previous;
          return criterionData.roots.find(id=>candidates.some(item=>item.id===id && item.map?.available))
            ?? candidates.find(item=>item.map?.available)?.id;
        });
        setError(undefined);
      } catch(reason) {
        if(!isAbort(reason))setError(errorMessage(reason));
      } finally {
        if(!controller.signal.aborted)timer=setTimeout(update,5000);
      }
    };
    void update();
    return()=>{controller.abort();clearTimeout(timer);};
  },[retry]);

  const choose=(item: Indicator)=>{
    if(!item.map?.available)return;
    setSelectedId(item.id);
  };
  const resetScroll=()=>scrollBody.current?.scrollTo({top:0});
  const panel=<div className="sidebar-content">
    <Tabs className="sidebar-tabs" activeKey={activeTab} onChange={key=>{setActiveTab(key);resetScroll();}}
      items={[{key:'criteria',label:'Критерии'},{key:'indicators',label:'Показатели'}]} />
    <div ref={scrollBody} className="sidebar-scroll" aria-label="Содержимое вкладки">
      <div hidden={activeTab!=='criteria'}>
        <CriteriaPanel items={criteria} roots={criteriaRoots} selectedId={selectedId} onSelect={choose} onNavigate={resetScroll} />
      </div>
      <div hidden={activeTab!=='indicators'}>
        <IndicatorPanel items={indicators} selectedId={selectedId} onSelect={choose} mobile={mobile} />
      </div>
    </div>
  </div>;

  return <Layout className="app-layout">
    <Layout.Header className="app-header">
      <Typography.Title level={4} className="brand">VUStBench</Typography.Title>
      {mobile && <Button icon={<BarChartOutlined />} onClick={()=>setDrawerOpen(true)}>Оценки</Button>}
    </Layout.Header>
    {error && <Alert type="error" title={error} action={<Button size="small" onClick={()=>setRetry(value=>value+1)}>Повторить</Button>} />}
    {city ? <Layout className="app-body">
      <Layout.Content className="map-content"><CityMap city={city} indicator={selected} /></Layout.Content>
      {!mobile && <Layout.Sider width={560} theme="light" className="app-sidebar">{panel}</Layout.Sider>}
      <Drawer title="Оценки" placement="right" open={mobile && drawerOpen} onClose={()=>setDrawerOpen(false)}
        size={560} className="mobile-sidebar">{panel}</Drawer>
    </Layout> : <Layout.Content className="initial-state"><Space><Spin /></Space></Layout.Content>}
  </Layout>;
}
