from contextlib import nullcontext
from app.services import cloud_planning as cp

def sources(monkeypatch, body):
    monkeypatch.setattr(cp,'transaction',lambda:nullcontext(None))
    monkeypatch.setattr(cp,'resource_access',lambda *args:{'cloud_allowed':True,'current_version':'v','title':'官方函数'})
    monkeypatch.setattr(cp,'one',lambda *args,**kwargs:{'body':body})
    return cp.authorized_sources({'actor':{},'execution':[{'skill_id':'functions'}],
        'sources':[{'resource_id':'r','version_id':'v','supported_skills':['functions'],
                    'locations':[{'skill':'functions','start':0,'end':min(300,len(body))}]}]})[0]

def test_later_example_survives_short_document(monkeypatch):
    body='def fib(n): print(n)\n'+'text '*700+'\ndef fib2(n):\n    result = []\n    return result'
    result=sources(monkeypatch,body)
    assert result['excerpt']==body
    assert result['coverage_scope']=='complete_stored_document'
    assert result['locations'][0]['end']==len(body)

def test_large_document_explicitly_partial_and_bounded(monkeypatch):
    result=sources(monkeypatch,'x'*9001)
    assert result['coverage_scope']=='partial_excerpt'
    assert len(result['excerpt'])==300
    assert result['stored_document_chars']==9001

def test_stack_activity_only_receives_eligible_then_actually_cited_versions():
    def src(v,declared):
        return dict(version_id=v,supported_skills=['stack'],declared_skills=declared,excerpt=v+' text')
    sources=[src('incidental',[]),src('class-stack',['stack']),src('other-stack',['stack'])]
    tasks=[dict(skill_id='stack',citations=[dict(version_id='class-stack')])]
    build=cp.activity_sources(sources,tasks)
    assert [(s['version_id'],s['excerpt'],s['cite_for_skill_ids']) for s in build]==[
        ('class-stack','class-stack text',['stack']),('other-stack','other-stack text',['stack'])]
    assert [s['version_id'] for s in cp.activity_sources(sources,tasks,review=True)]==['class-stack']

def test_resume_full_reread_vs_targeted_lookup():
    ref=[dict(skill_id='cpp',resume_proportion=.53,estimated_remaining_activity='接着完成函数练习，再自检。')]
    def check(t):return cp.resume_activity_issues(ref,[dict(skill_id='cpp',instructions=t)])
    assert check('阅读讲义《C++类型与引用》全文，再完成练习')
    assert not check('继续函数练习，必要时回看引用示例')
    assert not cp.resume_activity_issues([], [dict(skill_id='cpp',instructions='阅读讲义全文')])

def test_trim_separate_reread_keeps_practice_and_self_check():
    ref=[dict(skill_id='cpp',resume_proportion=.53,estimated_remaining_activity='继续练习并自检')]
    text='1. 阅读讲义全文；重点理解引用。2. 完成练习：编写三个函数。3. 自检作答：说明所有权。'
    assert cp.trim_completed_reading(ref,'cpp',text)=='1. 完成练习：编写三个函数。2. 自检作答：说明所有权。'
    assert cp.trim_completed_reading([], 'cpp',text)==text
    targeted=text.replace('阅读讲义全文','阅读材料中关于引用绑定的说明')
    assert cp.trim_completed_reading(ref,'cpp',targeted)==cp.trim_completed_reading(ref,'cpp',text)
    mixed=text.replace('重点理解引用','编写代码')
    assert cp.trim_completed_reading(ref,'cpp',mixed)==mixed
