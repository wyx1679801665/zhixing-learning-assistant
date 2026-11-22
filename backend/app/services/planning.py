"""A four-node LangGraph pipeline; local planning is explicitly deterministic."""
import json,re
from datetime import datetime
from typing import TypedDict
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from langgraph.graph import StateGraph,END
from app.core.db import *
from app.core.security import fail,class_access
from app.services.learning import SKILLS,mastery,allocate,fingerprint
from app.services.planning_rules import graph_snapshot,validate,completed_history

class State(TypedDict,total=False):
    run_id:str
    goal:dict
    actor:dict
    tasks:list
    sources:list
    schedule:list
    fingerprint:str
    path_id:str
    graph:list
    graph_version:str
    previous:dict
    mode:str
    triggers:list
    remediations:list
    preserved:list
    execution:list
    diff:dict
    rules:list
    budget_snapshot:dict
    reference_budget:dict
    preflight_built:bool
    reference_tasks:list
    cloud_calls:list
    cloud_mode:bool
    cloud_deadline:float
    cloud_outputs:dict
    cloud_issues:list
    cloud_approved:bool
    cloud_repairs:int
    last_agent:str
    learner_evidence:dict

def record_step(db,state,agent,detail):
    record=one(db,'SELECT status,retry_count FROM agent_runs WHERE id=:id',id=state['run_id'])
    if not record or record['status']=='cancelled':fail(409,'运行已取消')
    state['last_agent']=agent
    output={'task_ids':[t['id'] for t in state.get('tasks',[])],
            'source_versions':[s['version_id'] for s in state.get('sources',[])],
            'scope':state.get('mode'),'rules':state.get('rules',[])}
    output['model_calls']=[call for call in state.get('cloud_calls',[]) if call['agent']==agent]
    output['tools']={'GoalDiagnosisAgent':['goal.read','diagnosis.read','graph.snapshot','mastery.read'],
                     'PathBuildingAgent':['resources.authorized','budget.allocate'],
                     'PlanReviewAgent':['rules.validate'],
                     'LearningReplanAgent':['paths.persist','remediation.persist']}[agent]
    insert(db,'agent_steps',id=uid(),run_id=state['run_id'],agent=agent,status='succeeded',detail=detail,created_at=now(),
           input_fingerprint=state.get('fingerprint','{}'),output_summary=dump(output),attempt_no=record['retry_count'])
    run(db,'UPDATE agent_runs SET checkpoint=:c WHERE id=:id',c=dump(state),id=state['run_id'])

def record_cloud_step(state,agent,detail):
    with transaction() as db:record_step(db,state,agent,detail)

def step(state,agent,detail):
    if state.get('cloud_mode'):return
    with transaction() as db:record_step(db,state,agent,detail)

def source_changed(db,actor,source):
    resource=one(db,'SELECT current_version,status,scope,owner_id FROM resources WHERE id=:id',id=source['resource_id'])
    if not resource or resource['current_version']!=source['version_id']:
        return True
    private_owner=resource['scope']=='private' and resource['owner_id']==actor['id']
    return resource['status']!='published' and not private_owner

def diagnose(state):
    with transaction() as db:
        r=one(db,'SELECT * FROM agent_runs WHERE id=:id',id=state['run_id'])
        g=one(db,'SELECT * FROM goals WHERE id=:id',id=r['goal_id']);u=one(db,'SELECT id,name,username,role,status FROM users WHERE id=:id',id=r['actor_id'])
        if not g or g['status']=='archived' or u['status']!='active': fail(409,'目标不可用')
        if g['class_id'] and class_access(db,u,g['class_id'])['status']!='active':fail(409,'关联班级已归档')
        skills=json.loads(g['skills'])
        from app.services.engineering_goals import validate_scope
        validate_scope(db,g['id'])
        available_graph,_=graph_snapshot(db,g['id'])
        if not skills or not set(skills)<=set(s['id'] for s in available_graph): fail(409,'新主题缺少已验证图谱、题库和来源；当前只支持本地函数/递归主题','COVERAGE_GAP')
        diagnosis=one(db,'SELECT * FROM diagnoses WHERE goal_id=:g AND goal_version=:v AND attempt_id IS NOT NULL ORDER BY created_at DESC LIMIT 1',g=g['id'],v=g['version'])
        if not diagnosis:fail(409,'请先提交诊断')
        graph,version=graph_snapshot(db,g['id'])
        from app.services.course_planning import binding,closure,validate_versions
        course_binding=binding(db,g['id'])
        if course_binding:
            from app.services.curriculum_structure import extend_graph
            graph=extend_graph(db,course_binding['course_id'],json.loads(course_binding['mappings']),graph)
        questions=json.loads(diagnosis['questions'])
        validate_versions(db,questions)
        required=closure(graph,skills)
        missing=required-{q['skill'] for q in questions}
        if missing:fail(409,'目标或先修尚未测评，请重新领取覆盖完整范围的诊断：'+','.join(sorted(missing)),'PREREQUISITE_DIAGNOSIS_GAP')
        graph=[{**node,'assessment_source':{'bank_version':diagnosis['bank_version'],
                'question_ids':[q['id'] for q in questions if q['skill']==node['id']]}} for node in graph]
        previous=one(db,"SELECT * FROM paths WHERE goal_id=:g AND status='active'",g=g['id'])
        budget=one(db,'SELECT * FROM budgets WHERE user_id=:u',u=u['id'])
        from app.services.planning_evidence import learner_evidence,evidence_changes
        evidence=learner_evidence(db,u['id'],g,graph,diagnosis)
        evidence['catalog_evidence_integrated']=bool(course_binding)
        if course_binding:evidence['course_id']=course_binding['course_id']
        mode='initial';triggers=[]
        if r['kind']=='replan':
            mode='local'
            current=mastery(db,u['id'])
            if any(current.get(skill,{}).get('probability',.2)<.45 for skill in skills):triggers.append('掌握薄弱')
            overdue=rows(db,"SELECT a.id FROM assignments a JOIN tasks t ON t.id=a.task_id WHERE a.student_id=:u AND a.status NOT IN ('completed','cancelled') AND t.status='published' AND t.due_at<:time ORDER BY t.due_at",u=u['id'],time=now())
            if len(overdue)>=2:triggers.append('全账户有至少两项教师任务逾期未完成')
            old=json.loads(previous['validation']) if previous else {}
            triggers.extend(evidence_changes(old.get('learner_evidence'),evidence))
            before=old.get('budget_snapshot',budget)
            change=max(abs(budget[k]-before[k])/max(before[k],1) for k in ('daily_minutes','weekly_minutes'))
            if change:triggers.append('全局预算变化')
            if change>.30:mode='global'
            if previous and old.get('graph_version','curated-v1')!=version:
                mode='global';triggers.append('图谱版本变化')
            if previous and set(json.loads(old.get('goal_snapshot',g)['skills']))!=set(skills):
                mode='global';triggers.append('目标范围变化')
            if not previous:mode='global'
            if previous and json.loads(previous['fingerprint']).get('goal')!=g['version']:triggers.append('目标版本变化')
            for source in json.loads(previous['sources']) if previous else []:
                if source_changed(db,u,source):
                    triggers.append('来源证据变化');break
            if not triggers:triggers.append('学生主动评估')
        state.update(goal=g,actor=u,fingerprint=fingerprint(db,u['id'],g['id']),graph=graph,graph_version=version,
                     previous=previous,budget_snapshot=budget,mode=mode,triggers=triggers,learner_evidence=evidence)
    step(state,'GoalDiagnosisAgent','读取已提交诊断和已确认技能；未调用云端模型')
    return state

def required_skills(goal,graph):
    selected=set(json.loads(goal['skills']))
    for node in reversed(graph):
        if node['id'] in selected:selected.update(node['prerequisites'])
    return selected


def citation_for_skill(source,skill):
    citation={'resource_id':source['resource_id'],'version_id':source['version_id']}
    location=next((item for item in source.get('locations',[]) if item.get('skill')==skill),None)
    if location:
        citation['start']=location['start']
        citation['end']=location['end']
        citation['excerpt']=location.get('excerpt','')
    return citation


def preferred_sources_for_skill(sources,skill):
    supported=[source for source in sources if skill in source['supported_skills']]
    declared=[source for source in supported if skill in source.get('declared_skills',[])]
    return declared or supported

def source_location(body,skill,skill_tokens,declared_skills,semantic_locations):
    declared=skill in declared_skills or skill.rsplit('-',1)[-1] in declared_skills
    if declared and len(body)<=2000:
        return 0,len(body)
    matches=[match for token in skill_tokens if (match:=re.search(re.escape(token),body,re.IGNORECASE))]
    if matches:
        start=max(0,min(match.start() for match in matches)-80)
        return start,min(len(body),start+300)
    start,chunk=semantic_locations[skill]
    return start,min(len(body),start+len(chunk))


def no_pending_work(state):
    raise HTTPException(409, {'code':'NO_PENDING_LEARNING_TASKS',
        'message':'当前目标及先修知识点均已掌握，无需新增学习任务',
        'planning_fingerprint':state['fingerprint']})


def remaining_work(item, proportion):
    from decimal import Decimal
    base=Decimal(str(item.get('unrounded_minutes', item['minutes'])))
    result=base*(1-Decimal(str(proportion)))
    # JSON proportions are binary floats (e.g. 32/60). Ignore only round-trip noise.
    import math
    nearest=result.to_integral_value()
    if nearest>0 and abs(result-nearest)<=base*Decimal(str(math.ulp(float(proportion)))):return nearest
    return result

def build(state, record_step=True, *, cloud_preflight=False):
    from app.api.resources import visible
    from app.services.topic_bank import keywords
    tokens=keywords(state['graph'])
    semantic_vectors={}
    if state.get('cloud_mode'):
        try:
            from app.services.vector_index import embed
            target_skills=sorted(required_skills(state['goal'],state['graph']))
            semantic_vectors=dict(zip(target_skills,embed([skill+' '+next((node.get('name',skill) for node in state['graph'] if node['id']==skill),skill) for skill in target_skills])))
        except Exception:
            semantic_vectors={}
    with transaction() as db:
        g=state['goal'];u=state['actor'];m=mastery(db,u['id'])
        from app.services.course_planning import binding
        cb=binding(db,g['id'])
        from app.services.engineering_goals import context as project_context,PREFIX
        pc=project_context(db,g['id'])
        course_links=rows(db,'SELECT * FROM learning_material_links WHERE course_id=:c',c=cb['course_id']) if cb else []
        course_mapping={m['skill_id']:m['graph_skill_id'] for m in json.loads(cb['mappings'])} if cb else {}
        if pc:
            course_links=[link for sid in json.loads(pc['skill_ids']) for link in rows(db,'SELECT * FROM learning_material_links WHERE skill_id=:s',s=sid)]
            course_mapping={sid:PREFIX+sid for sid in json.loads(pc['skill_ids'])}
        target=required_skills(g,state['graph'])
        evidence_targets={skill for skill in target if m.get(skill,{}).get('probability',.2)<.75}
        sources=[]
        for resource in visible(db,u):
            if resource['status']!='published' and resource['scope']!='private':continue
            version=one(db,"SELECT * FROM resource_versions WHERE id=:id AND parse_status='ready' AND index_status='indexed'",id=resource['current_version'])
            if not version:continue
            if not cb and not pc and one(db,'SELECT id FROM learning_material_links WHERE resource_version_id=:v',v=version['id']):
                # A generic legacy topic must not silently borrow a similarly named
                # C/C++ course lesson. Course content requires an explicit identity map.
                continue
            supported=[skill for skill in evidence_targets if any(t.lower() in version['body'].lower() for t in tokens[skill])]
            if cb or pc:
                linked={course_mapping.get(link['skill_id']) for link in course_links if link['resource_version_id']==version['id']}
                supported=[skill for skill in supported if skill in linked]
            semantic_locations={}
            if semantic_vectors:
                from app.services.vector_index import cosine
                indexed=rows(db,'SELECT ordinal,body,embedding FROM vector_chunks WHERE version_id=:v ORDER BY ordinal',v=version['id'])
                for skill in evidence_targets-set(supported):
                    if (cb or pc) and skill not in linked:continue
                    if skill not in semantic_vectors:continue
                    matches=[(cosine(semantic_vectors[skill],json.loads(chunk['embedding'])),chunk) for chunk in indexed]
                    if matches:
                        score,chunk=max(matches,key=lambda candidate:candidate[0])
                        if score>=.55:
                            supported.append(skill)
                            semantic_locations[skill]=(chunk['ordinal']*700,chunk['body'])
            if supported:
                declared_skills=re.findall(r'(?m)^\s*知识点\s*`([a-zA-Z][\w-]*)`',version['body'])
                locations=[];fragments=[]
                for skill in supported:
                    start,end=source_location(version['body'],skill,tokens[skill],declared_skills,semantic_locations)
                    locations.append({'skill':skill,'start':start,'end':end,'excerpt':version['body'][start:end]})
                    fragments.append(version['body'][start:end])
                sources.append({'resource_id':resource['id'],'version_id':version['id'],'title':resource['title'],
                    'location':'正文字符 '+','.join(str(p['start'])+'-'+str(p['end']) for p in locations),
                    'excerpt':' … '.join(fragments),'locations':locations,
                    'supported_skills':supported,'declared_skills':declared_skills,
                    'cloud_allowed':bool(resource['cloud_allowed'])})
        gaps=evidence_targets-{skill for source in sources for skill in source['supported_skills']}
        if gaps:
            names={n['id']:n['name'] for n in state['graph']}
            raise HTTPException(409,{'code':'EVIDENCE_GAP','message':'授权正文覆盖不足：'+ '、'.join(names.get(s,s) for s in sorted(gaps))+'；请到课程知识库关联已发布正文或上传个人资料；仅有网址不算正文','missing_skills':sorted(gaps)})
        previous=state.get('previous')
        old={t['skill_id']:t for t in json.loads(previous['tasks'])} if previous else {}
        if previous:
            old.update({t['skill_id']:t for t in json.loads(previous['validation']).get('remediations',[])})
        preserved=completed_history(db,previous)
        completed={entry['item']['skill_id']:entry['item'] for entry in preserved}
        tasks=[];remediations=[];execution=[]
        for node in state['graph']:
            skill=node['id']
            if skill not in target or m.get(skill,{}).get('probability',.2)>=.75:continue
            item={**node,'skill_id':skill,'id':old.get(skill,{}).get('id',uid()) if state['mode']=='local' else uid(),
                  'assessment':'完成相关知识测验，达到0.75解锁后续学习',
                  'reinforcement':m.get(skill,{}).get('probability',.2)<.45,
                  'citations':[citation_for_skill(s,skill) for s in preferred_sources_for_skill(sources,skill)]}
            if skill in completed:
                item.update(id=uid(),kind='remediation',source_item_id=completed[skill]['id'])
                remediations.append(item)
            else:
                tasks.append(item)
            previous_item=old.get(skill)
            if previous_item:
                partial=one(db,'SELECT proportion FROM checkins WHERE path_id=:p AND item_id=:i',p=previous['id'],i=previous_item['id'])
                proportion=partial['proportion'] if partial else 0
                if proportion<1:
                    import math
                    item['original_minutes']=previous_item.get('original_minutes',previous_item['minutes'])
                    remaining=remaining_work(previous_item,proportion)
                    item['unrounded_minutes']=str(remaining)
                    item['minutes']=max(1,math.ceil(remaining))
                    item['remaining_minutes']=item['minutes']
                    # Only reuse activities when the same source versions still support them.
                    if proportion>0 and {c['version_id'] for c in previous_item.get('citations',[])}=={c['version_id'] for c in item['citations']}:
                        from app.services.daily_activities import remaining_activities
                        pending=remaining_activities(previous_item,proportion)
                        if pending:
                            item['resume_proportion']=proportion
                            item['instructions']='。'.join(f'{n}. {step}' for n,step in enumerate(pending,1))

            execution.append(item)
        minutes=sum(item['minutes'] for item in execution)
        if cloud_preflight:
            # Retained work has a fixed duration; new work can be resized by the model.
            minimum=sum(item.get('remaining_minutes',1) for item in execution)
            available=minutes
            if minutes:
                try:
                    allocate(db,u['id'],minutes,g['due_date'],g['id'],g['weekly_minutes'])
                except HTTPException as exc:
                    if not isinstance(exc.detail,dict) or exc.detail.get('code')!='BUDGET_CONFLICT':
                        raise
                    available=exc.detail['budget_conflict']['available_minutes']
                    if available<minimum:
                        allocate(db,u['id'],minimum,g['due_date'],g['id'],g['weekly_minutes'])
            fixed_minutes=sum(item.get('remaining_minutes',0) for item in execution)
            state['reference_budget']={'reference_minutes':minutes,'schedulable_reference_minutes':available,
                                       'fixed_remaining_minutes':fixed_minutes,
                                       'available_for_adjustable_tasks':available-fixed_minutes,
                                       'shortfall_minutes':minutes-available}
            schedule=[]
        else:
            schedule=allocate(db,u['id'],minutes,g['due_date'],g['id'],g['weekly_minutes']) if minutes else []
        pending=[{'item':item,'remaining':item['minutes']} for item in execution]
        for item in execution:item['dates']=[]
        for day in schedule:
            available=day['minutes']
            for planned in pending:
                amount=min(available,planned['remaining'])
                if amount:planned['item']['dates'].append({'date':day['date'],'minutes':amount})
                planned['remaining']-=amount;available-=amount
                if not available:break
        old_remaining={}
        old_progress={}
        for skill,item in old.items():
            checked=one(db,'SELECT proportion FROM checkins WHERE path_id=:p AND item_id=:i',p=previous['id'],i=item['id']) if previous else None
            import math
            proportion=checked['proportion'] if checked else 0
            old_progress[skill]=proportion
            old_remaining[skill]=max(0,math.ceil(remaining_work(item,proportion)))
        new_remaining={item['skill_id']:item['minutes'] for item in execution}
        diff={'added':sorted({t['skill_id'] for t in tasks}-set(old)),
              'removed':sorted(set(old)-{t['skill_id'] for t in tasks}),
              'kept':sorted(set(old)&{t['skill_id'] for t in tasks}),
              'reinforced':[t['skill_id'] for t in remediations],
              'before_schedule':json.loads(previous['validation']).get('schedule',[]) if previous else [],
              'after_schedule':schedule,'scope':state['mode'],'risks':state['triggers'],
              'before_remaining_minutes':sum(old_remaining.values()),
              'after_remaining_minutes':sum(new_remaining.values()),
              'remaining_by_skill':[{'skill_id':skill,'before':old_remaining.get(skill,0),'after':new_remaining.get(skill,0),
                                     'original_minutes':old.get(skill,{}).get('original_minutes',old.get(skill,{}).get('minutes')),
                                     'previous_plan_minutes':old.get(skill,{}).get('minutes'),
                                     'completion_proportion':old_progress.get(skill)}
                                    for skill in sorted(set(old_remaining)|set(new_remaining))]}
        if not execution:
            no_pending_work(state)
        from app.services.daily_activities import attach_daily_activities
        for scheduled_item in execution:attach_daily_activities(scheduled_item)
        state.update(tasks=tasks,sources=sources,schedule=schedule,remediations=remediations,
                     preserved=preserved,execution=execution,diff=diff)
    if record_step:step(state,'PathBuildingAgent','按先修与掌握度构造学习及独立补强任务，核算全局预算')
    return state

def review(state):
    with transaction() as db:state['rules']=validate(state,db)
    if not all(rule['passed'] for rule in state['rules']):
        raise HTTPException(409,{'code':'PATH_RULE_FAILED','message':'路径硬规则未通过：'+','.join(rule['rule_id'] for rule in state['rules'] if not rule['passed']),'rules':state['rules']})
    step(state,'PlanReviewAgent','逐条通过VR-001至VR-008')
    return state

def save(state):
    with transaction() as db:
        r=one(db,'SELECT * FROM agent_runs WHERE id=:id',id=state['run_id'])
        if r['status']=='cancelled': fail(409,'运行已取消')
        if fingerprint(db,state['actor']['id'],state['goal']['id'])!=state['fingerprint']: fail(409,'生成过程中输入已变化，请重试','PLANNING_INPUT_CHANGED')
        pid=uid()
        validation={key:state[key] for key in ('graph','graph_version','rules','remediations','preserved','execution','diff','mode','triggers','budget_snapshot')}
        validation.update(is_valid=True,schedule=state['schedule'],method='cloud-four-agent-v1' if state.get('cloud_mode') else 'local-deterministic-v2',cloud_called=bool(state.get('cloud_mode')),cloud_outputs=state.get('cloud_outputs',{}),model_config_id=r['model_config_id'],
                          previous=state['previous']['id'] if state.get('previous') else None,goal_snapshot=state['goal'])
        validation['learner_evidence']=state.get('learner_evidence',{})
        if state.get('reference_budget'):
            validation['reference_budget']=state['reference_budget']
        from app.services.engineering_goals import context as project_context,event_key
        if project_context(db,state['goal']['id']):validation['project_event_key']=event_key(db,state['goal']['id'])
        insert(db,'paths',id=pid,goal_id=state['goal']['id'],student_id=state['actor']['id'],status='pending_acceptance',
               content_status='verified',tasks=dump(state['tasks']),sources=dump(state['sources']),validation=dump(validation),
               fingerprint=state['fingerprint'],reason='依据诊断、图谱和全局预算生成，范围 '+state['mode'],created_at=now())
        from app.services.topic_bank import question_bank
        from app.services.course_planning import binding,bank as course_bank
        cb=binding(db,state['goal']['id'])
        bank=course_bank(db,cb,'practice') if cb else question_bank(db,'practice',state['graph_version'] if state['graph_version']!='curated-v1' else None)[0]
        for item in state['execution']:
            insert(db,'path_question_bank',path_id=pid,item_id=item['id'],questions=dump([question for question in bank if question['skill']==item['skill_id']]))
        for item in state['remediations']:
            insert(db,'remediation_tasks',id=item['id'],path_id=pid,skill=item['skill_id'],source_item_id=item['source_item_id'],
                   status='candidate',minutes=item['minutes'],created_at=now())
        state['path_id']=pid
        record_step(db,state,'LearningReplanAgent','云端解释调整原因，保存待接受路径及独立补强候选' if state.get('cloud_mode') else '保存待接受路径及独立补强候选')
        run(db,"UPDATE agent_runs SET status='succeeded',result_id=:p,finished_at=:t WHERE id=:id",p=pid,t=now(),id=state['run_id'])
    return state

graph=StateGraph(State)
for name,fn in [('diagnose',diagnose),('build',build),('review',review),('save',save)]: graph.add_node(name,fn)
graph.set_conditional_entry_point(lambda state:{'GoalDiagnosisAgent':'build','PathBuildingAgent':'review','PlanReviewAgent':'save'}.get(state.get('last_agent'),'diagnose'),{'diagnose':'diagnose','build':'build','review':'review','save':'save'});graph.add_edge('diagnose','build');graph.add_edge('build','review');graph.add_edge('review','save');graph.add_edge('save',END)
workflow=graph.compile()

def execute(run_id):
    try:
        with transaction() as db:
            claimed=run(db,"UPDATE agent_runs SET status='running',finished_at=NULL WHERE id=:id AND status='queued'",id=run_id)
            if claimed.rowcount!=1:return
            record=one(db,'SELECT * FROM agent_runs WHERE id=:id',id=run_id)
            state=json.loads(record['checkpoint'])
            if state.get('fingerprint')!=fingerprint(db,record['actor_id'],record['goal_id']):state={}
            state['run_id']=run_id
        if record['execution_mode']=='cloud':
            import time
            from app.services.cloud_planning import workflow as cloud_workflow
            state.update(cloud_mode=True,cloud_deadline=time.time()+180,cloud_repairs=0,cloud_calls=[])
            cloud_workflow.invoke(state,{'recursion_limit':20})
        else:
            workflow.invoke(state)
    except Exception as exc:
        message=exc.detail.get('message') if hasattr(exc,'detail') and isinstance(exc.detail,dict) else '规划执行失败，请检查日志与输入'
        code=exc.detail.get('code') if hasattr(exc,'detail') and isinstance(exc.detail,dict) else None
        outcome='no_work' if code=='NO_PENDING_LEARNING_TASKS' else 'failed'
        with transaction() as db:
            record=one(db,'SELECT * FROM agent_runs WHERE id=:id',id=run_id)
            if record and record['status'] not in ('cancelled','succeeded'):
                checkpoint=json.loads(record['checkpoint'])
                if outcome=='no_work' and record['kind']=='replan' and exc.detail.get('planning_fingerprint'):
                    user_id,goal_id=record['actor_id'],record['goal_id']
                    if fingerprint(db,user_id,goal_id)!=exc.detail['planning_fingerprint']:
                        outcome,code,message='failed','PLANNING_INPUT_CHANGED','生成过程中输入已变化，请重试'
                    else:
                        budget=one(db,'SELECT timezone FROM budgets WHERE user_id=:u',u=user_id)
                        today=datetime.now(ZoneInfo(budget['timezone'])).date().isoformat()
                        active=one(db,"SELECT id FROM paths WHERE goal_id=:g AND student_id=:u AND status='active'",g=goal_id,u=user_id)
                        if active:
                            params=dict(p=active['id'],g=goal_id,u=user_id,d=today)
                            where="source_type='path' AND source_id=:p AND goal_id=:g AND student_id=:u AND status='active' AND local_date>=:d AND minutes>0"
                            released=one(db,'SELECT COALESCE(SUM(minutes),0) AS total FROM reservations WHERE '+where,**params)['total']
                            if released:
                                run(db,"UPDATE reservations SET status='cancelled' WHERE "+where,**params)
                                touch(db,user_id)
                                message+=f'。已释放 {released} 分钟的学习预约'
                phase={'GoalDiagnosisAgent':'PathBuildingAgent','PathBuildingAgent':'PlanReviewAgent','PlanReviewAgent':'LearningReplanAgent'}.get(checkpoint.get('last_agent'),'GoalDiagnosisAgent')
                if isinstance(getattr(exc,'detail',None),dict):phase=exc.detail.get('cloud_failure_agent',phase)
                budget_conflict=exc.detail.get('budget_conflict') if code=='BUDGET_CONFLICT' else None
                insert(db,'agent_steps',id=uid(),run_id=run_id,agent=phase,status='no_work' if outcome=='no_work' else 'failed',detail=message,
                       created_at=now(),input_fingerprint=checkpoint.get('fingerprint','{}'),
                       output_summary=dump({'error':message,'code':code,'budget_conflict':budget_conflict,'missing_skills':exc.detail.get('missing_skills',[]) if hasattr(exc,'detail') and isinstance(exc.detail,dict) else [],'rules':exc.detail.get('rules',[]) if hasattr(exc,'detail') and isinstance(exc.detail,dict) else []}),attempt_no=record['retry_count'])
            run(db,"UPDATE agent_runs SET status=:s,error=:e,finished_at=:t WHERE id=:id AND status NOT IN ('cancelled','succeeded')",s=outcome,e=message,t=now(),id=run_id)

