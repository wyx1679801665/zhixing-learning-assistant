"""A student's unchanged private draft must not masquerade as a source change."""
from app.core.db import transaction, one, run
from app.services.planning import source_changed
from test_platform import P, classroom, resource, goal, planning


def test_private_draft_source_change_matches_acceptance_policy(clients):
    cid,_=classroom(clients)
    rid=resource(clients,cid)
    with transaction() as db:
        version=one(db,'SELECT current_version FROM resources WHERE id=:i',i=rid)['current_version']
        source={'resource_id':rid,'version_id':version}
        actor={'id':'student'}
        assert not source_changed(db,actor,source)
        run(db,"UPDATE resources SET owner_id='student',class_id=NULL,scope='private',status='draft' WHERE id=:i",i=rid)
        assert not source_changed(db,actor,source)
        assert source_changed(db,{'id':'student2'},source)
        assert source_changed(db,actor,{**source,'version_id':'old-version'})
        run(db,"UPDATE resources SET scope='class',class_id=:c,status='draft' WHERE id=:i",c=cid,i=rid)
        assert source_changed(db,actor,source)


def test_active_path_warns_when_its_source_is_withdrawn(clients):
    cid,_=classroom(clients)
    rid=resource(clients,cid)
    gid=goal(clients,cid)
    pid=planning(clients['student'],gid)
    assert clients['student'].post(P+f'/paths/{pid}/accept').status_code==200
    assert clients['student'].get(P+f'/paths/{pid}').json()['source_refresh_required'] is False
    with transaction() as db:
        run(db,"UPDATE resources SET status='withdrawn' WHERE id=:i",i=rid)
    active=clients['student'].get(P+f'/paths/{pid}')
    assert active.status_code==200
    assert active.json()['status']=='active' and active.json()['source_refresh_required'] is True


def test_resource_revision_replan_keeps_old_path_until_new_one_is_accepted(clients):
    cid,_=classroom(clients)
    rid=resource(clients,cid)
    gid=goal(clients,cid)
    student=clients['student']
    old_id=planning(student,gid)
    assert student.post(P+f'/paths/{old_id}/accept').status_code==200
    teacher=clients['teacher']
    original=teacher.get(P+f'/resources/{rid}').json()
    revised=teacher.post(P+f'/resources/{rid}/versions',data={'expected_version':original['version']},
                         files={'file':('revised.txt','Python函数 function def return\n递归 recursion 结束条件\n调用栈 stack 后进先出\n增加返回顺序练习'.encode(),'text/plain')})
    assert revised.status_code==201,revised.text
    assert teacher.post(P+f'/resources/{rid}/publish').status_code==200
    old=student.get(P+f'/paths/{old_id}').json()
    assert old['status']=='active' and old['source_refresh_required'] is True
    preview_id=planning(student,gid,replan=True)
    preview=student.get(P+f'/paths/{preview_id}').json()
    assert preview['status']=='pending_acceptance'
    assert '来源证据变化' in preview['validation']['triggers']
    assert student.get(P+f'/paths/{old_id}').json()['status']=='active'
    assert student.post(P+f'/paths/{preview_id}/accept').status_code==200
    assert student.get(P+f'/paths/{old_id}').json()['status']=='superseded'
    assert student.get(P+f'/paths/{preview_id}').json()['status']=='active'
