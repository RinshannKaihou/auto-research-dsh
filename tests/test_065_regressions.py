import json
import sqlite3

import pytest
from auto_research.native_store import NativeStore
from auto_research.service import NativeService


def call(service, method, operation, session='main', **fields):
    return service.handle(dict(transport_id=operation, operation_id=operation, host_id='host', session_id=session, method=method, **fields))['value']


def setup(tmp_path):
    service = NativeService(tmp_path/'registry.sqlite3')
    root = tmp_path/'project'
    call(service,'open','open',root=str(root),goal='SECRET_PROJECT_CANARY')
    call(service,'focus','focus',role='planner',mode='manual')
    (root/'sample.txt').write_text('允许读取的样本α\n' * 6000)
    (root/'truth-key.txt').write_text('SECRET_TRUTH_CANARY')
    pub = call(service,'publish','pub',status='partial',summary='SECRET_PUBLICATION_CANARY',items=[{'item_id':'sample','source_path':'sample.txt'},{'item_id':'truth','source_path':'truth-key.txt'}])
    node=call(service,'propose','propose',question='SECRET_NODE_CANARY',why_now='now',plan='SECRET_PLAN_CANARY',root_reason='independent')
    call(service,'open','core-open',session='core',root=str(root),session_role='node_core',node_id=node['node_id'])
    call(service,'focus','core-focus',session='core',node_id=node['node_id'],role='core',mode='auto')
    ref=pub['refs'][0]
    return service,root,node,ref


def test_blind_context_and_material_access(tmp_path):
    service,root,node,ref=setup(tmp_path)
    fields=dict(purpose='domain',label='review',prompt='Classify supplied samples',inputs=[ref],context_mode='blind')
    task=call(service,'specialist_create','create',session='core',fields=fields)
    call(service,'specialist_bind_child','bind',session='core',task_id=task['task_id'],child_session_id='expert')
    context=call(service,'memory_context','context',session='expert')['text']
    assert 'input-1' in context
    assert 'SECRET_' not in context and ref not in context and 'sample.txt' not in context
    pieces=[];offset=0
    while offset is not None:
        page=call(service,'specialist_read_input',f'read-{offset}',session='expert',input_id='input-1',offset=offset,limit=123,model_call=True)
        pieces.append(page['text']);offset=page['next_offset']
        assert set(page)=={'input_id','text','next_offset'}
    assert ''.join(pieces)==(root/'sample.txt').read_text()
    with pytest.raises(ValueError,match='not assigned'):
        call(service,'specialist_read_input','forbidden',session='expert',input_id='input-2',model_call=True)
    with pytest.raises(ValueError,match='Blind reviewers'):
        call(service,'query','query',session='expert',ref=node['node_id'],model_call=True)
    with pytest.raises(ValueError,match='cannot modify'):
        call(service,'note','note',session='expert',body='no',model_call=True)


def test_identity_is_bound_and_main_only_consolidates(tmp_path):
    service,root,node,ref=setup(tmp_path)
    fields=dict(purpose='domain',label='review',prompt='review',inputs=[])
    with pytest.raises(ValueError,match='Main coordinates'):
        call(service,'specialist_create','main-domain',fields=fields)
    with pytest.raises(ValueError,match='match'):
        call(service,'specialist_create','wrong-node',session='core',fields={**fields,'node_id':'X-other'})
    task=call(service,'specialist_create','core-domain',session='core',fields=fields)
    assert task['node_id']==node['node_id'] and task['attempt_id']
    review=call(service,'specialist_create','project-review',fields={**fields,'purpose':'review'})
    assert review['node_id'] is None
    with pytest.raises(ValueError,match='frozen'):
        call(service,'specialist_create','bad-blind',session='core',fields={**fields,'context_mode':'blind','inputs':[node['question_ref']]})


def test_schema6_migration_preserves_results_and_defaults(tmp_path):
    store=NativeStore(tmp_path/'project');store.initialize('old','init')
    task=store.specialist_create(dict(parent_session_id='main',purpose='review',label='old',prompt='old',inputs=[]),'old')
    store.specialist_finish(dict(task_id=task['task_id'],parent_session_id='main',state='completed',result={'output':'legacy'},exit_verified=True),'finish')
    with sqlite3.connect(store.db_path) as db:
        db.execute('ALTER TABLE specialist_tasks DROP COLUMN context_mode')
        db.execute('PRAGMA user_version=5')
    upgraded=NativeStore(store.root)
    old=upgraded.specialist_get(task['task_id'])
    assert old['result']=={'output':'legacy'} and old['context_mode']=='research'
    assert upgraded.control_state()['schema_version']==9
    with sqlite3.connect(store.meta/'schema-5-backup.sqlite3') as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==5
        assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    with sqlite3.connect(store.db_path) as db:
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    assert NativeStore(store.root).specialist_get(task['task_id'])==old
