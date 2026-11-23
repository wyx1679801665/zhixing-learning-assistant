from datetime import date,timedelta
import pytest
from app.api import discovery
from test_platform import P
from test_route_contrasts import seed_route_fixture, CPP

QUOTE='四元数旋转使用单位四元数，练习中首先验证归一化条件，再比较旋转前后向量长度。'


def test_norm_formula_requires_more_than_rotation_formula():
    assert not discovery.has_norm_formula("旋转后的点为 p′ = q p q^{-1}。")
    assert discovery.has_norm_formula("三维向量的长度为 √(x² + y² + z²)。")
    assert discovery.has_norm_formula(r"\sqrt{x^{2}+y^{2}+z^{2}}")
    assert not discovery.has_norm_formula(r"|q|=\sqrt{w^2+x^2+y^2+z^2}=1")
    assert discovery.has_norm_formula(r"|q|=\sqrt{w^2+x^2+y^2+z^2}；|v|=\sqrt{x^2+y^2+z^2}")
    assert not discovery.has_norm_formula("四元数的范数由四个分量平方和定义。")
    assert discovery.has_quaternion_rotation_formula(r"p'=q\,p\,q^{-1}")
    assert discovery.has_quaternion_rotation_formula("p′ = q · (0,v) · q*")
    assert not discovery.has_quaternion_rotation_formula("q = [cos(θ/2), sin(θ/2)·u]")
    assert discovery.quaternion_norm_gap("验证 ||v|| = ||v′||",["进行旋转。"])
    assert discovery.quaternion_norm_gap("分别计算 ‖v‖ 与 ‖v′‖",["√(x²+y²+z²)"]) is None
    assert discovery.quaternion_norm_gap("证明四元数旋转保长",["√(x²+y²+z²)"])
    assert discovery.quaternion_norm_gap("证明四元数旋转保长",["利用四元数范数的乘法性"]) is None


def test_miss_trace_starts_at_initial_interval():
    case=dict(values=[1,3,5,7,9],target=4,trace=[[0,1,0],[1,1,1]])
    assert not discovery.complete_miss_trace(case)
    case['trace'].insert(0,[0,4,2])
    assert discovery.complete_miss_trace(case)
    activity=discovery.binary_miss_practice()
    assert discovery.miss_trace_in_activity(case,activity)
    assert not discovery.miss_trace_in_activity(case,'初始化 left=0,right=4；目标 4 不存在，返回 -1。')
    assert '第 1 轮：左界 0，右界 4，中点 2' in activity
    assert '第 2 轮：左界 0，右界 1，中点 0' in activity
    assert '第 3 轮：左界 1，右界 1，中点 1' in activity
    assert '左界 2 > 右界 1，区间为空，返回 -1' in activity


def test_default_schedule_keeps_required_chain_and_excludes_optional_blocker():
    nodes=[dict(ref='intro',prerequisites=[]),
           dict(ref='algorithm',prerequisites=['intro']),
           dict(ref='optional',prerequisites=['intro'],ready=False),
           dict(ref='practice',prerequisites=['algorithm'])]
    draft=dict(nodes=nodes,goal_checks=[dict(refs=['practice'])])
    assert discovery.default_schedule_selection(draft)==['intro','algorithm','practice']
    draft['goal_checks'].append(dict(refs=[]))
    assert discovery.default_schedule_selection(draft)==[node['ref'] for node in nodes]


@pytest.mark.parametrize('target,index,valid',[(3,2,True),(3,3,True),(3,4,False),(3,-1,False),(0,None,True)])
def test_search_example_duplicate_and_absent(target,index,valid):
    assert discovery.valid_search_example(dict(values=[1,2,3,3,4,5,10],target=target,index=index)) is valid


def test_missing_private_rubric_has_actionable_gap():
    with pytest.raises(ValueError,match='请先提供'):
        discovery.discover('根据尚未提供的本班专用评分细则计算项目分数',[],set(),{})


def test_quaternion_embedding_example_uses_declared_scalar_position():
    mixed='嵌入为纯四元数p = (P, 0)；输入示例：P = (1, 0, 0)，则p = (0, 1, 0, 0)。'
    assert discovery.contradictory_embedding(mixed)
    assert not discovery.contradictory_embedding(mixed.replace('(P, 0)','(0, P)'))
    assert not discovery.contradictory_embedding(mixed.replace('(0, 1, 0, 0)','(1, 0, 0, 0)'))


def test_invalid_practice_format_retries_once_without_web_search(monkeypatch):
    draft=dict(nodes=[dict(ref='s',ready=True,sources=[dict(quote=QUOTE)])],
               goal_checks=[dict(requirement='验证长度',refs=[])],searches=[])
    issues=[]
    def propose(agent,payload):
        issues.append(payload['format_issue'])
        return dict(source_ids=[1],minutes='十分钟',activity='计算两次长度。'),'mock'
    monkeypatch.setattr(discovery,'model_call',propose)
    discovery.supplement_missing_activity('验证长度',draft)
    assert issues==[None,'minutes须为1至60整数']
    assert draft['practice_format_retries']==1 and draft['practice_initial_format_issue']==issues[1]
    assert 'minutes' in draft['practice_repair_issue'] and len(draft['nodes'])==1 and not draft['searches']


def test_coordinate_caption_cannot_certify_length_check(monkeypatch):
    node=dict(ref='r',ready=True,activity='计算并比较旋转前后的长度。',sources=[dict(quote='在这个旋转下新的坐标')])
    def model(agent,p):
        if agent=='DiscoveryCitationReview':
            assert p['claims']==[dict(id=1,requirement='验证长度相等',excerpts=['在这个旋转下新的坐标'])]
            return dict(supported_ids=[]),'mock'
        return dict(checks=[dict(requirement='验证长度相等',supports=[
            dict(ref='r',activity_step=1,quote_id=1,first_line=1,last_line=1)])]),'mock'
    monkeypatch.setattr(discovery,'model_call',model)
    check,=discovery.review_goal('旋转验证',[node])
    assert not check['refs'] and '原文不足' in check['gap']


@pytest.mark.parametrize('first_ids',[[],[1]])
def test_practice_search_uses_remaining_budget_and_new_verbatim_source(monkeypatch,first_ids):
    original=dict(ref='s1',ready=True,sources=[dict(quote='定义')])
    draft=dict(nodes=[original],searches=[{} for _ in range(5)],goal_checks=[dict(requirement='核对长度',refs=[])])
    def propose(agent,payload):
        if len(payload['sources'])==1:
            return dict(source_ids=first_ids,minutes=12,activity='核对长度',search_terms=['vector norm']),'mock'
        return dict(source_ids=[2],minutes=12,activity='分别计算两个向量的模长再比较。'),'mock'
    monkeypatch.setattr(discovery,'model_call',propose)
    monkeypatch.setattr(discovery,'search_web',lambda q:[dict(url='https://example.edu/norm')])
    monkeypatch.setattr(discovery,'fetch_page',lambda url:dict(url=url,title='长度',body=QUOTE))
    def review(goal,nodes,requirements):
        supported=nodes[0]['sources'][0]['quote']==QUOTE
        return [dict(requirement='核对长度',refs=['goal-practice'] if supported else [])]
    monkeypatch.setattr(discovery,'review_goal',review)
    discovery.supplement_missing_activity('验证旋转',draft)
    assert len(draft['searches'])==6 and draft['nodes'][-1]['sources'][0]['quote']==QUOTE
    assert draft['nodes'][-1]['prerequisites']==[]


def test_external_draft_end_to_end(clients,monkeypatch):
    seed_route_fixture()
    calls=[]
    def model(agent,p):
        if agent=='DiscoveryCitationReview':
            return dict(supported_ids=[1]),'mock'
        if agent=='DiscoveryGoalReview':
            item=p['activities'][-1]
            return {'checks':[dict(requirement='完成目标练习',supports=[dict(ref=item['ref'],evidence=item['activity'],quote_id=1,first_line=1,last_line=1)])]},'mock'
        calls.append(agent)
        if agent=='DiscoveryCatalogMatch':
            return {'ids':[CPP] if CPP in [c['id'] for c in p['catalog']] else []},'model'
        if agent=='DiscoveryOutline':
            return {'steps':[dict(name='类型',catalog_id=CPP,objective='使用数值类型',minutes=20,prerequisites=[]),
                             dict(name='单位四元数旋转',catalog_id=None,objective='验证向量长度',minutes=40,prerequisites=[1])]},'model'
        return dict(source=1,quote=QUOTE,activity='计算并比较旋转前后的长度'), 'model'
    monkeypatch.setattr(discovery,'model_call',model)
    monkeypatch.setattr(discovery,'search_web',lambda q:[{'url':'https://example.edu/rotation'}])
    monkeypatch.setattr(discovery,'fetch_page',lambda url:dict(title='讲义',url=url,body=QUOTE))
    s=clients['student']; url=P+'/discovery-drafts'
    body=dict(text='四元数旋转',weekly_minutes=30,due_date=str(date.today()+timedelta(days=30)),web_consent=True)
    headers={'Idempotency-Key':'trial'}
    assert s.post(url,json={**body,'web_consent':False},headers=headers).status_code==422
    ident=s.post(url,json=body,headers=headers).json()['id']
    detail=s.get(url+'/'+ident).json()
    assert detail['status']=='ready',detail
    local,external=detail['nodes']
    assert local['catalog_id']==CPP and external['catalog_id'] is None
    assert external['sources'][0]['quote']==QUOTE
    assert sum(d['minutes'] for d in detail['schedule'])==60
    assert clients['student2'].get(url+'/'+ident).status_code==404
    assert clients['student2'].get(url).json()==[]
    assert s.patch(url+'/'+ident,json=dict(revision=1,selected=['step-2'])).status_code==422
    saved=s.patch(url+'/'+ident,json=dict(revision=1,selected=['step-1'])).json()
    assert saved['status']=='partial' and saved['goal_gaps']
    assert saved['revision']==2 and sum(d['minutes'] for d in saved['schedule'])==20
    assert s.patch(url+'/'+ident,json=dict(revision=1,selected=[])).status_code==409
    count=len(calls)
    assert s.post(url,json=body,headers=headers).json()['id']==ident and len(calls)==count


def test_empty_catalog_and_unavailable_evidence(clients,monkeypatch):
    monkeypatch.setattr(discovery,'model_call',lambda a,p:({'steps':[dict(name='新知识',catalog_id=None,
        prerequisites=[],objective='解释新概念',minutes=30)]},'model') if a=='DiscoveryOutline' else ({'ids':[]},'model'))
    monkeypatch.setattr(discovery,'search_web',lambda q:[])
    s=clients['student'];url=P+'/discovery-drafts'
    ident=s.post(url,json=dict(text='新目标',web_consent=True,weekly_minutes=30,due_date=str(date.today()+timedelta(days=15))),
                 headers={'Idempotency-Key':'empty'}).json()['id']
    d=s.get(url+'/'+ident).json()
    assert d['status']=='partial' and d['blocked']==['step-1'] and d['schedule']==[]


def test_fully_external_goal_returns_sourced_schedule(clients,monkeypatch):
    def model(agent,payload):
        if agent=='DiscoveryCitationReview':
            return dict(supported_ids=[1]),'mock'
        if agent=='DiscoveryGoalReview':
            item=payload['activities'][-1]
            return {'checks':[dict(requirement='完成目标练习',supports=[dict(ref=item['ref'],evidence=item['activity'],quote_id=1,first_line=1,last_line=1)])]},'mock'
        if agent=='DiscoveryCatalogMatch':
            return {'ids':[]},'mock'
        if agent=='DiscoveryOutline':
            return {'steps':[dict(name='单位四元数',catalog_id=None,prerequisites=[],
                objective='验证旋转保持长度',minutes=25,search_terms=['quaternion'])]},'mock'
        return {'source':1,'passage':1,'additional':[dict(source=2,passage=1),dict(source=9,passage=1)],
                'activity':'给出旋转前后各一组坐标，比较向量长度。'},'mock'
    monkeypatch.setattr(discovery,'model_call',model)
    second='分别计算原向量和旋转后向量的平方和，并比较这两个结果。'
    def search(q):
        paths=('rotation','length') if 'worked example' in q else ('rotation',)
        return [dict(url='https://example.edu/'+p) for p in paths]
    monkeypatch.setattr(discovery,'search_web',search)
    monkeypatch.setattr(discovery,'fetch_page',lambda url:dict(url=url,title='旋转',body=second if url.endswith('length') else QUOTE))
    s=clients['student']; url=P+'/discovery-drafts'
    initial=s.post(url,json=dict(text='验证四元数旋转',weekly_minutes=60,
        due_date=str(date.today()+timedelta(days=14)),web_consent=True),headers={'Idempotency-Key':'external'}).json()
    d=s.get(url+'/'+initial['id']).json()
    assert d['status']=='ready' and d['nodes'][0]['catalog_id'] is None
    assert [log['pages'] for log in d['searches']]==[1,1] and d['nodes'][0]['sources'][0]['fingerprint']
    assert [s['quote'] for s in d['nodes'][0]['sources']]==[QUOTE,second]
    assert sum(day['minutes'] for day in d['schedule'])==25
    assert s.get(P+'/goals').json()==[]


def test_evidence_window_finds_deep_passage_and_retains_original():
    passage='Unit quaternion represents a rotation; conjugation preserves vector length.'
    body='Navigation menu. '*450 + passage + ' Footer.'*1000
    window=discovery.evidence_window(body,['unit quaternion','vector length'])
    assert passage in window and window in body and len(window)==5000


def test_reverse_suggestion_preserves_catalog_order_and_real_activity(clients,monkeypatch):
    from test_route_contrasts import GEO,MOVE
    seed_route_fixture()
    def model(agent,payload):
        if agent=='DiscoveryCitationReview':
            return dict(supported_ids=[1]),'mock'
        if agent=='DiscoveryGoalReview':
            item=payload['activities'][-1]
            return {'checks':[dict(requirement='完成目标练习',supports=[dict(ref=item['ref'],evidence=item['activity'],quote_id=1,first_line=1,last_line=1)])]},'mock'
        if agent=='DiscoveryCatalogMatch':
            return {'ids':[n['id'] for n in payload['catalog'] if n['id'] in (GEO,MOVE)]},'mock'
        if agent=='DiscoveryEvidence':
            return dict(source=1,quote=QUOTE,activity='有具体产出的练习'),'mock'
        return {'steps':[dict(name=name,catalog_id=cid,prerequisites=deps,objective='构造例子并验证',minutes=20)
                         for name,cid,deps in [('单位四元数',None,[]),('坐标变换',MOVE,[1]),('几何表示',GEO,[2])]]},'mock'
    monkeypatch.setattr(discovery,'model_call',model)
    monkeypatch.setattr(discovery,'search_web',lambda q:[{'url':'https://example.edu/rotation'}])
    monkeypatch.setattr(discovery,'fetch_page',lambda url:dict(url=url,title='旋转',body=QUOTE))
    s=clients['student'];url=P+'/discovery-drafts'
    ident=s.post(url,json=dict(text='四元数',weekly_minutes=90,due_date=str(date.today()+timedelta(days=30)),
                 web_consent=True),headers={'Idempotency-Key':'reverse'}).json()['id']
    d=s.get(url+'/'+ident).json()
    assert d['status']=='ready',d
    assert d['omitted_suggestions']==[dict(step='step-3',prerequisite='step-2')]
    assert [n['ref'] for n in d['nodes']]==['step-1','step-3','step-2']
    assert '提交例子' in d['nodes'][0]['activity'] and sum(s['minutes'] for s in d['schedule'])==60


def test_scheduled_basics_do_not_substitute_for_missing_goal_operations(monkeypatch):
    nodes=[dict(ref='s1',ready=True,minutes=30,activity='计算给定向量的模长',sources=[])]
    required=['用四元数计算旋转后的向量','计算并比较旋转前后的模长']
    monkeypatch.setattr(discovery,'model_call',lambda *args:({'checks':[
        dict(requirement=req,supports=[],gap=req) for req in required]},'mock'))
    draft=dict(nodes=nodes,schedule=[dict(ref='s1',minutes=30)],goal_checks=discovery.review_goal('旋转并验证保距',nodes))
    assert discovery.assess_schedule(draft)=='partial' and draft['goal_gaps']==required


def test_invalid_outline_never_schedules_partial_prefix(clients,monkeypatch):
    seed_route_fixture()
    step=dict(name='类型',catalog_id=CPP,objective='编写函数',minutes=20,prerequisites=[])
    calls=[]
    def model(agent,payload):
        calls.append(agent)
        return ({'ids':[CPP]} if agent=='DiscoveryCatalogMatch' else {'steps':[step,dict(step,prerequisites=[3])]}),'mock'
    monkeypatch.setattr(discovery,'model_call',model)
    s=clients['student'];url=P+'/discovery-drafts'
    ident=s.post(url,json=dict(text='函数',weekly_minutes=30,due_date=str(date.today()+timedelta(days=14)),
                 web_consent=True),headers={'Idempotency-Key':'invalid-outline'}).json()['id']
    result=s.get(url+'/'+ident).json()
    assert calls.count('DiscoveryOutlineRepair')==1
    assert result['status']=='partial' and '先修编号' in result['error']
    assert result['nodes']==result['schedule']==result['searches']==[]


def test_duplicate_catalog_id_keeps_operations_and_remaps_prerequisites():
    seed_route_fixture()
    catalog=[dict(id=CPP,name='类型')]
    step=dict(name='类型',catalog_id=CPP,objective='声明类型',minutes=20,prerequisites=[])
    nodes,_=discovery.validate_outline(dict(steps=[
        step,dict(step,name='使用类型',objective='验证类型',prerequisites=[1]),
        dict(step,catalog_id=None,name='库外练习',prerequisites=[2])
    ]),catalog,{CPP})
    assert [n['ref'] for n in nodes]==['step-1','step-3']
    assert nodes[0]['objective']=='声明类型；验证类型' and nodes[0]['minutes']==40
    assert nodes[0]['prerequisites']==[] and nodes[1]['prerequisites']==['step-1']


def test_activity_formula_cannot_replace_missing_source_excerpt(monkeypatch):
    node=dict(ref='rotation',ready=True,activity='计算 q p q*',sources=[dict(quote='这里叫三明治变换')])
    support=dict(ref='rotation',evidence='计算 q p q*',source_evidence='q p q*')
    monkeypatch.setattr(discovery,'model_call',lambda *args: (
        dict(checks=[dict(requirement='执行旋转',supports=[support])]),'mock'))
    check,=discovery.review_goal('四元数旋转',[node])
    assert not check['refs'] and not check['citations'] and '外部引文' in check['gap']


def test_formula_excerpt_preserves_original_and_requires_same_symbols():
    text='计算 q * p\n * q⁻¹，再提取虚部。'
    assert discovery.original_excerpt(text,'q*p*q⁻¹')=='q * p\n * q⁻¹'
    assert discovery.original_excerpt(text,'q*p*q¹') is None
    assert discovery.original_excerpt('a b','ab') is None


def test_review_uses_only_selected_original_formula_lines(monkeypatch):
    quote="旋转结果为：\np' = q p q^{-1}\r\n取虚部。"
    node=dict(ref='r',ready=True,activity='计算旋转后的向量',sources=[dict(quote=quote)])
    support=dict(ref='r',activity_step=1,quote_id=1,first_line=2,last_line=3)
    def model(agent,payload):
        if agent=='DiscoveryCitationReview':
            return dict(supported_ids=[1]),'mock'
        assert payload['activities'][0]['quotes'][0]['lines'][1]['line']==2
        return dict(checks=[dict(requirement='计算旋转',supports=[support])]),'mock'
    monkeypatch.setattr(discovery,'model_call',model)
    citation=discovery.review_goal('旋转',[node])[0]['citations'][0]
    assert citation['excerpt']==quote.split('\n',1)[1] and citation['activity_excerpt']==node['activity']
    support['activity_step']=2
    assert not discovery.review_goal('旋转',[node])[0]['refs']
    support['activity_step']=1
    support['last_line']=4
    assert not discovery.review_goal('旋转',[node])[0]['refs']
def test_practice_repair_only_credits_original_gap_with_reviewed_evidence(monkeypatch):
    node=dict(ref='step-1',ready=True,sources=[dict(quote='比较向量的平方和')])
    missing=dict(requirement='比较长度',refs=[])
    draft=dict(nodes=[node],goal_checks=[missing])
    monkeypatch.setattr(discovery,'model_call',lambda *args: (
        dict(source_ids=[1],activity='分别计算平方和并比较。',minutes=10),'mock'))
    def review(goal,nodes,requirements):
        assert requirements==['比较长度'] and nodes[0]['prerequisites']==['step-1']
        return [dict(requirement='其他要求',refs=['goal-practice'])]
    monkeypatch.setattr(discovery,'review_goal',review)
    discovery.supplement_missing_activity('验证旋转',draft)
    assert draft['nodes']==[node] and missing['refs']==[]
    monkeypatch.setattr(discovery,'review_goal',lambda *args: [dict(requirement='比较长度',refs=['goal-practice'])])
    discovery.supplement_missing_activity('验证旋转',draft)
    assert draft['nodes'][-1]['minutes']==10 and missing['refs']==['goal-practice']
