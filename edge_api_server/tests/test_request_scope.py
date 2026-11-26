import json
import re
import sqlite3
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.database import Database
from app.errors import ApiException
from app.lims_requests import NUMBER_FIELDS, TEXT_FIELDS, scoped_requests
from app.request_scope import RequestScope, parse_request_map

NAMES = [
    ('XRD-01','XRD 정성분석','XRD'), ('001','X선 회절 분석','XRD'), ('002','X-ray diffraction','XRD'),
    ('XRF-01','X선 형광분석',None), ('F01','FT-IR 분석','FTIR'), ('F02','적외선 분석','FTIR'),
    ('R01','라만 분광 분석','RAMAN'), ('R02','Raman spectrum','RAMAN'),
    ('T01','TEM 이미지','TEM'), ('T02','STEM 분석','TEM'), ('T03','TEM-EDS 원소 매핑','TEM'),
    ('T04','STEM EDS Map','TEM'), ('T05','TEM 코팅층 두께','TEM'), ('T06','투과 전자현미경','TEM'),
    ('T07','Transmission electron microscopy','TEM'), ('E01','EDS 원소 분석',None),
    ('E02','EDX Mapping',None), ('C01','코팅층 두께',None), ('C02','전자현미경',None),
    ('S01','SEM-EDS',None), ('S02','주사 전자현미경',None), ('SYSTEM','시스템 시험',None),
    ('S03','TEM/SEM 전자현미경',None), ('ALL','FTIR / XRD 동시 시험',None), ('EDS-TEM','EDS TEM','TEM'),
]

@pytest.mark.parametrize('project', ['FTIR','RAMAN','XRD','TEM'])
@pytest.mark.parametrize('mapped', [False, True])
def test_sql_and_live_classification_agree(project, mapped):
    methods = (('E01','TEM'),('C01','TEM'),('XRD-01','RAMAN')) if mapped else ()
    legacy = (('T04','TEM'),('T05','FTIR')) if mapped else ()
    scope = RequestScope(project, methods, (), legacy)
    with sqlite3.connect(':memory:') as db:
        db.create_function('regexp',2,lambda pattern,value: bool(re.search(pattern,value or '')))
        db.execute('CREATE TABLE lims_req_ax_search(test_mtd_code TEXT,test_mtd_name TEXT,test_state INT)')
        db.executemany('INSERT INTO lims_req_ax_search VALUES(?,?,7)',[(c,n) for c,n,_ in NAMES])
        where,params=Database._request_filter_sql(experiment_scope=scope)
        actual={r[0] for r in db.execute('SELECT test_mtd_code FROM lims_req_ax_search '+where,params)}
    assert actual=={c for c,n,_ in NAMES if scope.matches(c,n)}
    if not mapped: assert actual=={c for c,_,p in NAMES if p==project}
    if mapped and project=='TEM': assert actual=={'E01','C01'}
    if mapped and project=='FTIR': assert 'T05' in actual
    if project=='XRD': assert 'XRF-01' not in actual


def test_map_preserves_real_identifiers_and_rejects_conflicts():
    parsed=parse_request_map('001-A=TEM,EDS/02=STEM','test')
    assert parsed==(('001-A','TEM'),('EDS/02','TEM'))
    scope=RequestScope('TEM',parsed)
    assert scope.matches('001-A','Unknown') and not scope.matches('001A','TEM')
    clause,params=RequestScope('TEM',(("X' OR 1=1 --",'TEM'),)).sql()
    assert "X'" not in clause and params==["x' or 1=1 --"]
    for raw in ('broken','A=unknown','A=TEM,a=XRD','A\nB=TEM'):
        with pytest.raises(ValueError): parse_request_map(raw,'test')


def test_real_mariadb_classifies_the_same_names_and_preserves_code_priority(mariadb):
    import pymysql
    config = {**mariadb, 'database': mariadb['name']}
    del config['name']
    with pymysql.connect(**config, charset='utf8mb4') as db:
        with db.cursor() as cursor:
            cursor.execute('CREATE TABLE lims_req_ax_search(test_mtd_code VARCHAR(200),test_mtd_name VARCHAR(200),test_state INT)')
            cursor.executemany('INSERT INTO lims_req_ax_search VALUES(%s,%s,7)',[(c,n) for c,n,_ in NAMES])
            for mapped in [False, True]:
                for project in ['FTIR','RAMAN','XRD','TEM']:
                    scope = RequestScope(project, (('E01','TEM'),('C01','TEM'),('XRD-01','RAMAN')) if mapped else (),
                                         (), (('T04','TEM'),('T05','FTIR')) if mapped else ())
                    where, params = Database._request_filter_sql(experiment_scope=scope)
                    cursor.execute('SELECT test_mtd_code FROM lims_req_ax_search '+where.replace('?', '%s'), params)
                    assert {r[0] for r in cursor.fetchall()} == {c for c,n,_ in NAMES if scope.matches(c,n)}


def test_legacy_mapping_keeps_identifier_normalization_and_asset_case():
    scope = RequestScope('TEM', legacy_map=(('M001','TEM'),), asset_map=(('AX-01','TEM'),))
    assert scope.matches('-M-001-', 'Unclassified')
    _, trusted = scope.query_filters({'assetNumber': 'ax-01'})
    assert trusted


def test_settings_load_lookup_maps_separately(monkeypatch):
    monkeypatch.setenv('RIST_REQUEST_METHOD_MAP','EDS-001=TEM,C-002=TEM,F-001=FT-IR')
    monkeypatch.setenv('RIST_REQUEST_ASSET_MAP','00042=STEM')
    monkeypatch.setenv('RIST_ANALYSIS_TYPE_MAP','ROUTE-01=XRD')
    config=Settings.from_env()
    assert config.request_method_map==(('EDS-001','TEM'),('C-002','TEM'),('F-001','FTIR'))
    assert config.request_asset_map==(('00042','TEM'),)
    assert config.analysis_type_map==(('ROUTE01','XRD'),)


def row(number,code,name,request='REQ-1'):
    item={key:None for key in NUMBER_FIELDS|TEXT_FIELDS}
    item.update(reqResultNo=1,reqNumber=request,smpResultNo=number,testMtdResultNo=number,
                testMtdCode=code,testMtdName=name,testState=7,reqDate='2026-11-23',outputOrder=number)
    return item


class Relay:
    def __init__(self,items,assets=None): self.items,self.assets,self.calls=items,assets or {},[]
    def fetch(self,page,size,filters):
        self.calls.append((page,size,dict(filters)))
        items=self.items
        if filters.get('testMtdCode'): items=[r for r in items if r['testMtdCode']==filters['testMtdCode']]
        if filters.get('assetNumber'): items=[r for r in items if r['testMtdResultNo'] in self.assets.get(filters['assetNumber'],[])]
        start=(page-1)*size
        return {'page':page,'size':size,'totalCount':len(items),'items':items[start:start+size]}


def test_filters_all_upstream_pages_before_total_and_edge_pagination(monkeypatch):
    monkeypatch.setattr('app.lims_requests.SCOPE_BATCH_SIZE',2)
    relay=Relay([row(1,'R','Raman'),row(2,'S','SEM'),row(3,'T','TEM'),row(4,'T','STEM'),row(5,'T','TEM-EDS'),row(6,'E','EDS')])
    a=scoped_requests(relay,RequestScope('TEM'),1,2,{'testState':'1,3,5,7'})
    b=scoped_requests(relay,RequestScope('TEM'),2,2,{'testState':'1,3,5,7'})
    assert a['totalCount']==b['totalCount']==3
    assert [r['smpResultNo'] for r in a['items']]==[3,4]
    assert [r['smpResultNo'] for r in b['items']]==[5]
    assert len(relay.calls)==6 and all(f=={'testState':'1,3,5,7'} for _,_,f in relay.calls)
    assert '매핑' in a['scopeNote']


def test_tem_methods_assets_union_and_duplicate_test_rows(monkeypatch):
    monkeypatch.setattr('app.lims_requests.SCOPE_BATCH_SIZE',2)
    methods=tuple((m,'TEM') for m in ['M1','M2','M3','M4'])
    assets=(('AX1','TEM'),('AX2','TEM'))
    relay=Relay([row(1,'M1','TEM'),row(2,'M2','STEM'),row(3,'M3','EDS'),row(4,'M4','코팅층'),row(5,'M5','SEM')],
                {'AX1':[1,2,3],'AX2':[2,3,4,5]})
    result=scoped_requests(relay,RequestScope('TEM',methods,assets),1,50,{'fromDate':'2026-11-01'})
    assert result['totalCount']==4
    assert [r['testMtdCode'] for r in result['items']]==['M1','M2','M3','M4']
    assert len(relay.calls)==8 and all(f['fromDate']=='2026-11-01' for _,_,f in relay.calls)


def test_single_known_method_uses_remote_pagination_and_keeps_conditions():
    relay=Relay([row(i,'M1','EDS') for i in range(1,20)])
    result=scoped_requests(relay,RequestScope('TEM',(('M1','TEM'),)),3,5,{'reqNumber':'REQ-1'})
    assert result['totalCount']==19 and len(result['items'])==5
    assert relay.calls==[(3,5,{'reqNumber':'REQ-1','testMtdCode':'M1'})]


def test_registered_asset_resolves_eds_but_manual_unknown_asset_does_not():
    relay=Relay([row(1,'E1','EDS')],{'AX1':[1],'AX2':[1]})
    scope=RequestScope('TEM',asset_map=(('AX1','TEM'),))
    assert scoped_requests(relay,scope,1,50,{})['totalCount']==1
    assert scoped_requests(relay,scope,1,50,{'assetNumber':'AX2'})['totalCount']==0


@pytest.mark.parametrize('filters',[{'testMtdCode':'OTHER'},{'assetNumber':'OTHER'}])
def test_other_experiment_filters_fail_before_network(filters):
    relay=Relay([])
    scope=RequestScope('TEM',(('T','TEM'),('OTHER','XRD')),(('OTHER','XRD'),))
    with pytest.raises(ApiException) as error: scoped_requests(relay,scope,1,50,filters)
    assert error.value.code=='REQUEST_SCOPE_MISMATCH' and not relay.calls


def test_large_and_changing_results_never_produce_partial_success(monkeypatch):
    monkeypatch.setattr('app.lims_requests.SCOPE_MAX_ITEMS',2)
    relay=Relay([row(i,'T','TEM') for i in range(3)])
    with pytest.raises(ApiException) as error: scoped_requests(relay,RequestScope('TEM'),1,50,{})
    assert error.value.code=='REQUEST_SCOPE_TOO_BROAD'
    monkeypatch.setattr('app.lims_requests.SCOPE_MAX_ITEMS',100)
    monkeypatch.setattr('app.lims_requests.SCOPE_BATCH_SIZE',2)
    original=relay.fetch
    def changed(page,size,filters):
        result=original(page,size,filters)
        if page>1: result['totalCount']+=1
        return result
    relay.fetch=changed
    with pytest.raises(ApiException) as error: scoped_requests(relay,RequestScope('TEM'),1,50,{})
    assert error.value.code=='LIMS_REQUESTS_CHANGED'


def test_ignored_exact_method_condition_is_an_error():
    relay=SimpleNamespace(fetch=lambda p,s,f: {'page':p,'size':s,'totalCount':1,'items':[row(1,'BAD','XRD')]})
    with pytest.raises(ApiException) as error: scoped_requests(relay,RequestScope('TEM',(('M1','TEM'),)),1,50,{})
    assert error.value.code=='LIMS_REQUESTS_INVALID_RESPONSE'


def test_http_live_scope_is_optional_for_generic_api_but_applied_for_each_experiment(monkeypatch,tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.lims_requests import LimsRequestsClient
    from tests.test_lims_requests import NoCacheAccess
    items=[row(i,c,n) for i,(c,n,_) in enumerate(NAMES,1)]
    relay=Relay(items)
    monkeypatch.setattr(LimsRequestsClient,'fetch',lambda self,p,s,f: relay.fetch(p,s,f))
    monkeypatch.setattr(Database,'from_settings',lambda _:NoCacheAccess())
    settings=Settings(storage_root=tmp_path,lims_requests_enabled=True,error_capture_files=False)
    client=TestClient(create_app(settings))
    headers={'X-Request-Id':'scope-test'}
    for project in ['FT-IR','RAMAN','XRD','TEM']:
        response=client.get('/api/v1/requests',params={'source':'lims','experimentType':project,'pageSize':2},headers=headers)
        assert response.status_code==200,response.text
        payload=response.json()
        expected={c for c,_,p in NAMES if p==project.replace('-','')}
        assert payload['totalCount']==len(expected)
        assert all(item['testMethodCode'] in expected for item in payload['items'])
        assert payload['scopeNote'] and payload['experimentType']==project
        assert relay.calls[-1][2]['testState']=='1,3,5,7'
    all_states=client.get('/api/v1/requests',params={'source':'lims','experimentType':'TEM','includeCompleted':'true'},headers=headers)
    assert all_states.status_code==200 and relay.calls[-1][2]=={}
    generic=client.get('/api/v1/requests',params={'source':'lims'},headers=headers)
    assert generic.status_code==200 and generic.json()['totalCount']==len(items)
    assert relay.calls[-1][2]=={}


def test_scoped_cached_queries_use_same_scope_for_count_and_page(monkeypatch,tmp_path):
    from app.service import EdgeService
    seen=[]
    class Cached:
        def fetch_request_summaries(self,*args,**kwargs):seen.append(kwargs);return []
        def count_request_summaries(self,**kwargs):seen.append(kwargs);return 0
    settings=Settings(storage_root=tmp_path,request_method_map=(('E1','TEM'),('C1','TEM')))
    result=EdgeService(settings,Cached()).request_summaries(1,200,'TEM')
    assert result['experimentType']=='TEM' and result['totalCount']==0
    assert len(seen)==2 and seen[0]['experiment_scope']==seen[1]['experiment_scope']
    assert seen[0]['experiment_scope'].matches('E1','EDS')
    assert not seen[0]['experiment_scope'].matches('XRD','XRD')


def test_duplicate_upstream_pages_and_call_time_budgets_fail_closed(monkeypatch):
    monkeypatch.setattr('app.lims_requests.SCOPE_BATCH_SIZE',1)
    relay=Relay([row(1,'T','TEM'),row(2,'T','TEM')])
    monkeypatch.setattr('app.lims_requests.SCOPE_MAX_CALLS',1)
    with pytest.raises(ApiException) as error:scoped_requests(relay,RequestScope('TEM'),1,50,{})
    assert error.value.code=='REQUEST_SCOPE_TOO_BROAD'
    monkeypatch.setattr('app.lims_requests.SCOPE_MAX_CALLS',32)
    original=relay.fetch
    def repeated(p,s,f):
        result=original(p,s,f)
        if p==2:result['items']=[row(1,'T','TEM')]
        return result
    relay.fetch=repeated
    with pytest.raises(ApiException) as error:scoped_requests(relay,RequestScope('TEM'),1,50,{})
    assert error.value.code=='LIMS_REQUESTS_CHANGED'
    monkeypatch.setattr('app.lims_requests.SCOPE_DEADLINE_SECONDS',0)
    relay.calls=[]
    with pytest.raises(ApiException) as error:scoped_requests(relay,RequestScope('TEM'),1,50,{})
    assert error.value.code=='REQUEST_SCOPE_TOO_BROAD' and not relay.calls


def test_all_live_lookup_omits_automatic_method_and_asset_maps_but_keeps_explicit_filters(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.lims_requests import LimsRequestsClient
    from tests.test_lims_requests import NoCacheAccess
    items = [row(1, 'A11112', '부품가공 (판형)', request='2022T00793')]
    relay = Relay(items, assets={'100000128': [1]})
    monkeypatch.setattr(LimsRequestsClient, 'fetch', lambda self,p,s,f: relay.fetch(p,s,f))
    monkeypatch.setattr(Database, 'from_settings', lambda _: NoCacheAccess())
    settings = Settings(storage_root=tmp_path, lims_requests_enabled=True, error_capture_files=False,
                        request_method_map=(('XRD-METHOD','XRD'),), request_asset_map=(('XRD-ASSET','XRD'),))
    params = {'source':'lims', 'includeCompleted':'true', 'reqNumber':'2022T00793', 'pageSize':200}
    client = TestClient(create_app(settings))
    scoped = client.get('/api/v1/requests', params={**params,'experimentType':'XRD'}, headers={'X-Request-Id':'scoped'})
    assert scoped.status_code == 200 and scoped.json()['totalCount'] == 0
    assert relay.calls[-1][2] == {'reqNumber':'2022T00793','testMtdCode':'XRD-METHOD','assetNumber':'XRD-ASSET'}
    all_rows = client.get('/api/v1/requests', params=params, headers={'X-Request-Id':'all'})
    assert all_rows.status_code == 200 and all_rows.json()['totalCount'] == 1
    assert relay.calls[-1][2] == {'reqNumber':'2022T00793'}
    explicit = client.get('/api/v1/requests', params={**params,'assetNumber':'100000128','testMtdCode':'A11112','testState':'5'},
                          headers={'X-Request-Id':'all-explicit'})
    assert explicit.status_code == 200 and explicit.json()['items'][0]['testMethodCode'] == 'A11112'
    assert relay.calls[-1][2] == {'reqNumber':'2022T00793','assetNumber':'100000128','testMtdCode':'A11112','testState':'5'}


class ContainsRelay:
    def __init__(self, items):
        self.items, self.calls = items, []

    def fetch(self, page, size, filters):
        self.calls.append((page, size, dict(filters)))
        items = self.items
        for key in ('reqNumber', 'testMtdCode'):
            if filters.get(key):
                items = [item for item in items if filters[key].casefold() in item[key].casefold()]
        start = (page - 1) * size
        return {'page': page, 'size': size, 'totalCount': len(items), 'items': items[start:start + size]}


def test_contains_scope_filters_complete_result_before_count_and_pagination(monkeypatch):
    monkeypatch.setattr('app.lims_requests.SCOPE_BATCH_SIZE', 2)
    relay = ContainsRelay([row(1, 'A11111', 'XRD'), row(2, 'A11112', 'TEM'), row(3, 'A11113', 'XRD'), row(4, 'A11114', 'XRD')])
    filters = {'matchMode': 'CONTAINS', 'testMtdCode': 'A111', 'reqNumber': 'REQ'}
    scope = RequestScope('XRD', asset_map=(('100000128', 'XRD'),))
    first = scoped_requests(relay, scope, 1, 2, filters)
    second = scoped_requests(relay, scope, 2, 2, filters)
    assert first['totalCount'] == second['totalCount'] == 3
    assert [item['testMtdCode'] for item in first['items']] == ['A11111', 'A11113']
    assert [item['testMtdCode'] for item in second['items']] == ['A11114']
    assert len(relay.calls) == 4
    assert all(query == filters for _, _, query in relay.calls)


def test_contains_of_mapped_method_cannot_take_exact_fast_path_or_trust_partial_assets():
    relay = ContainsRelay([row(1, 'A11111', 'unknown'), row(2, 'A111110', 'XRD')])
    scope = RequestScope('XRD', method_map=(('A11111', 'XRD'),), asset_map=(('100000128', 'XRD'),))
    filters = {'matchMode': 'CONTAINS', 'testMtdCode': 'A11111', 'assetNumber': '128'}
    queries, trusted = scope.query_filters(filters)
    assert queries == [filters] and not trusted
    result = scoped_requests(relay, scope, 1, 50, filters)
    assert result['totalCount'] == 1 and result['items'][0]['testMtdCode'] == 'A11111'


def test_contains_does_not_treat_metacharacters_as_wildcards_or_accept_ignored_filters():
    item = row(1, 'A%_001', 'XRD')
    relay = SimpleNamespace(fetch=lambda page, size, filters: {'page': page, 'size': size, 'totalCount': 1, 'items': [item]})
    result = scoped_requests(relay, RequestScope('XRD'), 1, 50, {'matchMode': 'CONTAINS', 'testMtdCode': 'A%_'})
    assert result['totalCount'] == 1
    for filters in ({'testMtdCode': 'AX_'}, {'reqNumber': 'OTHER'}):
        with pytest.raises(ApiException) as error:
            scoped_requests(relay, RequestScope('XRD'), 1, 50, {'matchMode': 'CONTAINS', **filters})
        assert error.value.code == 'LIMS_REQUESTS_INVALID_RESPONSE'
