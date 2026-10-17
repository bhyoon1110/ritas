import hashlib
from copy import deepcopy
from fastapi.testclient import TestClient
from app.xrd_workbench import integration as api
from app.xrd_workbench.render.draft_cache import DraftImageCache
from .test_integration import make_app, upload


def test_drafts_private_revisioned_and_separate_from_saved_reports(tmp_path):
    c=TestClient(make_app(tmp_path,auth=True),headers={'test-user':'one'})
    sid=upload(c,{'A.txt':b'10 1\n20 2\n'})
    url=api.BASE+f'/api/reports/{sid}'
    base=c.get(url).json()['document']; draft=deepcopy(base);draft['metadata']['title']=''
    payload=dict(document=draft,baseline=base,expected_revision=0)
    assert c.put(url+'/draft',json=payload).status_code==200
    recovered=c.get(url+'/draft');assert recovered.headers['cache-control']=='private, no-store'
    assert recovered.json()['document']['metadata']['title']==''
    assert c.get(url).json()['document']==base
    assert c.put(url+'/draft',json=payload).status_code==409
    c.headers['test-user']='two';assert c.get(url+'/draft').status_code==404
    c.headers['test-user']='one';c.headers['test-project']='TEM';assert c.get(url+'/draft').status_code==403
    c.headers['test-project']='XRD'
    assert c.post(url+'/draft/clear',json={'expected_revision':0}).status_code==409
    assert c.post(url+'/draft/clear',json={'expected_revision':1}).json()['document'] is None
    payload['document']['project_id']='wrong';payload['expected_revision']=2
    assert c.put(url+'/draft',json=payload).status_code==422


def chunk(c,sid,name,data,total,offset=0):
    return c.post(api.BASE+f'/api/upload-sessions/{sid}/chunks',data=dict(relative_path=name,offset=offset,total_size=total,chunk_index=0,chunk_count=1),files={'file':(name,data)})


def test_resume_hash_retries_never_truncate_or_mix_files_and_cancel_preserves(tmp_path):
    c=TestClient(make_app(tmp_path,auth=True),headers={'test-user':'one'})
    sid=c.post(api.BASE+'/api/upload-sessions').json()['upload_id'];url=api.BASE+f'/api/upload-sessions/{sid}'
    raw=b'#Intensity_unit=cps\n10 1\n20 8\n30 3\n';cut=17
    assert chunk(c,sid,'A.txt',raw[:cut],len(raw)).status_code==200
    saved=c.get(url).json()['files'][0]
    assert saved['uploaded_bytes']==cut and saved['prefix_sha256']==hashlib.sha256(raw[:cut]).hexdigest()
    assert chunk(c,sid,'A.txt',raw[cut:],len(raw),cut).status_code==200
    assert chunk(c,sid,'A.txt',raw[:cut],len(raw)).json()['uploaded_bytes']==len(raw)
    assert chunk(c,sid,'A.txt',b'BAD',len(raw)).status_code==409
    assert chunk(c,sid,'A.txt',b'BAD',len(raw),-1).status_code==400
    assert c.get(url).json()['files'][0]['prefix_sha256']==hashlib.sha256(raw).hexdigest()
    assert c.post(url+'/preflight').json()['files'][0]['points']==3
    c.headers['test-user']='two';assert c.get(url).status_code==404;assert c.get(api.BASE+'/api/upload-sessions').json()==[]
    c.headers['test-user']='one';assert c.post(url+'/cancel').status_code==200
    assert c.get(url).status_code==410
    assert any(p.read_bytes()==raw for p in tmp_path.rglob('A.txt'))


def test_resume_recovers_last_chunk_before_atomic_rename(tmp_path):
    c=TestClient(make_app(tmp_path));sid=c.post(api.BASE+'/api/upload-sessions').json()['upload_id']
    url=api.BASE+f'/api/upload-sessions/{sid}';raw=b'10 1\n20 2\n'
    assert chunk(c,sid,'A.txt',raw[:5],len(raw)).status_code==200
    partial=next(tmp_path.rglob('A.txt.part'))
    # Simulate shutdown after writing the final bytes, before rename/session save.
    partial.write_bytes(raw)
    response=c.get(url);assert 'no-store' in response.headers['cache-control']
    assert response.json()['files'][0]['completed']
    assert response.json()['files'][0]['prefix_sha256']==hashlib.sha256(raw).hexdigest()
    assert c.post(url+'/preflight').json()['ok']
    assert not partial.exists() and partial.with_suffix('').read_bytes()==raw


def test_preflight_reports_all_files_errors_and_exact_raw_values(tmp_path):
    c=TestClient(make_app(tmp_path));sid=c.post(api.BASE+'/api/upload-sessions').json()['upload_id']
    url=api.BASE+f'/api/upload-sessions/{sid}'
    for name,data in {'A.txt':b'#Intensity_unit=cps\n10,125\n10.02,138\n', 'B.txt':b'10 1\n10 2\n','C.txt':b'X Y Z\n'}.items():
        assert chunk(c,sid,name,data,len(data)).status_code==200
    result=c.post(url+'/preflight').json();assert not result['ok']
    assert result['files'][0]['x_range']==[10,10.02]
    assert result['files'][0]['preview'][0]==dict(line=2,x=10,y=125)
    assert '2행' in result['files'][1]['error'] and '1행' in result['files'][2]['error']
    assert not list(tmp_path.rglob('project.json'))


def test_cache_is_bounded_private_and_invalidates_changed_render_arguments():
    cache=DraftImageCache(max_bytes=10,max_entries=2);calls=[]
    def render(*args):calls.append(args);return b'1234'
    assert cache.render('one',render,{'x':[1,2]},100)==b'1234'
    cache.render('one',render,{'x':[1,2]},100);assert len(calls)==1
    cache.render('two',render,{'x':[1,2]},100);assert len(calls)==2
    cache.render('one',render,{'x':[1,3]},100);assert len(calls)==3
    assert cache.size==8 and len(cache.entries)==2
    cache.render('one',render,{'x':[1,2]},100);assert len(calls)==4
    cache.ttl=-1;cache.render('one',render,{'x':[1,2]},100);assert len(calls)==5


def test_text_edit_reuses_graph_but_graph_edit_renders_again():
    from .fixture_data import example_project
    from .test_report_export import sources
    from app.xrd_workbench.core.report import new_document
    from app.xrd_workbench.render.layout import build_pages
    project=example_project();doc=new_document(project);cache=DraftImageCache();calls=[]
    def image(*args):calls.append(args);return b'fake-test-image'
    render=lambda *args:cache.render('owner',image,*args)
    build_pages(project,doc,sources(project),image_renderer=render);count=len(calls);assert count
    doc['metadata']['title']='새 제목'
    build_pages(project,doc,sources(project),image_renderer=render);assert len(calls)==count
    doc['figure']['spacing']=99
    build_pages(project,doc,sources(project),image_renderer=render);assert len(calls)>count
