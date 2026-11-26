import json

from fastapi.testclient import TestClient
import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box

from vustbench_core.datasets import CityDataset
from vustbench_app.api import create_app
from vustbench_app.criteria import CriteriaStore
from vustbench_app.datasets import DatasetStore
from vustbench_app.indicators import IndicatorStore
from vustbench_app.runs import RunStore
from vustbench_app.settings import Settings


def test_criteria_api_partial_data_cache_invalidation_and_null_map(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path, artifacts_dir=tmp_path / 'artifacts')
    for filename in ['buildings.geojson', 'blocks.geojson', 'bn_service_types.json']:
        (tmp_path / filename).write_text('{}')
    (tmp_path / 'services').mkdir()
    (tmp_path / 'services/school.geojson').write_text('{}')
    nodes = [
        {'id':'school','name':'School','operation':'service','service':'school'},
        {'id':'library','name':'Library','operation':'service','service':'library'},
        {'id':'dimension','name':'Dimension','operation':'weighted_mean','dependencies':[
            {'id':'school','weight':.75},{'id':'library','weight':.25}]},
        {'id':'ksd','name':'K_SD','operation':'k_sd','dependencies':[{'id':'dimension'}]*4},
    ]
    config={'version':1,'model_version':'fixture','interpretation':'support_higher_is_better','roots':['ksd'],'nodes':nodes}
    config_path = tmp_path / 'social_support.json'
    config_path.write_text(json.dumps(config))
    buildings=gpd.GeoDataFrame({'population':[100,300]},geometry=[Point(.2,.2),Point(1.2,.2)],crs=4326,index=['b:0','b:1'])
    blocks=gpd.GeoDataFrame({'id':['block:0','block:1','block:2']},geometry=[box(0,0,1,1),box(1,0,2,1),box(2,0,3,1)],crs=4326,index=['block:0','block:1','block:2'])
    runs=RunStore(settings.artifacts_dir)
    datasets=DatasetStore(settings,runs)
    datasets._city=CityDataset(buildings,blocks,[0,0,3,1],{'data_version':'fixture'})
    indicators=IndicatorStore(settings,datasets,runs)
    run=runs.create('school','walk','ready')
    run.update(status='completed',data_version=indicators._data_version('school'),
               summary={'total_demand':10,'supplied_within':1})
    runs.write(run)
    (runs.directory('ready')/'buildings.parquet').touch()
    (runs.directory('ready')/'services.parquet').touch()
    blocks.assign(provision_value=[0,1,np.nan]).to_parquet(runs.directory('ready')/'blocks.parquet')
    with TestClient(create_app(settings,datasets)) as client:
        response=client.get('/api/criteria')
        assert response.status_code == 200
        payload=response.json()
        root=next(item for item in payload['items'] if item['id']=='ksd')
        assert abs(root['value']-.01)<1e-12
        assert next(item for item in payload['items'] if item['id']=='school')['value']==.1
        assert payload['aggregation']=='sum_demand_then_evaluate'
        assert root['coverage']==.75
        assert root['status']=='partial'
        features=client.get('/api/criteria/ksd/layers/blocks').json()['features']
        assert [f['properties']['support_value'] for f in features]==[0,1,None]
        assert [f['properties']['data_coverage'] for f in features]==[.75,.75,0]
        assert client.get('/api/criteria/ksd/layers/blocks?bbox=10,10,11,11').json()['features']==[]
        assert client.get('/api/criteria/unknown/layers/blocks').status_code==404
        assert client.get('/api/criteria/ksd/layers/blocks?bbox=bad').status_code==422
        assert client.get('/api/criteria/ksd/layers/buildings').status_code==404
    # A fresh store restores the saved snapshot without evaluating the graph.
    from vustbench_core import social
    with monkeypatch.context() as patch:
        patch.setattr(social,'evaluate_support',lambda *args: (_ for _ in ()).throw(AssertionError('cache missed')))
        restored=CriteriaStore(settings,datasets,indicators,runs).rows()
        assert restored==payload
    nodes[2]['dependencies'][0]['weight']=.25
    config_path.write_text(json.dumps(config))
    store=CriteriaStore(settings,datasets,indicators,runs)
    updated=store.rows()
    assert updated['version']!=payload['version']
    assert next(item for item in updated['items'] if item['id']=='ksd')['coverage']==.5
    # Changed service data invalidates the saved provision run and derived values.
    (tmp_path/'services/school.geojson').write_text('{"changed":true}')
    unknown=store.rows()
    root=next(item for item in unknown['items'] if item['id']=='ksd')
    assert root['value'] is None
    assert not root['map']['available']
    assert all(f['properties']['support_value'] is None for f in store.layer('ksd',None,12)['features'])
