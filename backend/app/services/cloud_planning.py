"""Four cloud agents generate bounded proposals through existing business services."""
import json
import hashlib
import time
from langgraph.graph import StateGraph,END
from app.core.db import transaction,one,dump
from app.core.security import fail,resource_access
from app.services import planning
from app.services.learning import allocate
from app.services.planning_rules import validate
from app.services.source_activity_review import ACTIVITY_RULES, source_activity_issues


def agent_call(state,agent,payload):
    from app.api.models import call_model
    if state.get('sources'):authorized_sources(state)
    with transaction() as db:
        record=one(db,'SELECT * FROM agent_runs WHERE id=:id',id=state['run_id'])
        if record['status']!='running':fail(409,'运行已取消或中断')
        if planning.fingerprint(db,state['actor']['id'],state['goal']['id'])!=state['fingerprint']:
            fail(409,'生成过程中输入已变化，请重新规划','PLANNING_INPUT_CHANGED')
        config=one(db,'SELECT * FROM model_configs WHERE id=:id',id=record['model_config_id'])
        quality=one(db,'SELECT review_status,case_count,passed_count FROM model_quality_reports WHERE model_config_id=:id ORDER BY created_at DESC LIMIT 1',id=record['model_config_id'])
        if not config or not quality or quality['review_status']!='approved' or quality['case_count']!=60 or quality['passed_count']!=60:
            fail(409,'运行绑定的模型配置需要完成质量复核','MODEL_QUALITY_REQUIRED')
    remaining=state['cloud_deadline']-time.time()
    if remaining<=0:fail(503,'四智能体运行超过180秒时限','MODEL_TIMEOUT')
    config['timeout_seconds']=min(config['timeout_seconds'],remaining)
    started=time.monotonic()
    try:
        response=call_model(config,dump({'agent':agent,'prompt_version':config['prompt_version'],'output_contract_version':'planning-v2','output_limits':{'summary_or_reason_max_chars':2000,'instructions_max_chars':4000,'string_array_max_items':20,'string_array_item_max_chars':500,'tasks_max_items':30,'citation_version_ids_max_items':8},'task':payload}))
    except Exception:
        state.setdefault('cloud_calls',[]).append({'agent':agent,'model_config_id':config['id'],'status':'failed','elapsed_ms':int((time.monotonic()-started)*1000),'usage':None})
        from app.core.db import run
        with transaction() as db:run(db,"UPDATE agent_runs SET checkpoint=:c WHERE id=:id AND status='running'",c=dump(state),id=state['run_id'])
        raise
    metadata=response.pop('_provider_meta',{})
    state.setdefault('cloud_calls',[]).append({'agent':agent,'model_config_id':config['id'],'model':config['model'],'prompt_version':config['prompt_version'],'status':'succeeded','elapsed_ms':int((time.monotonic()-started)*1000),'usage':metadata.get('usage'),'request_id':metadata.get('request_id')})
    if time.time()>state['cloud_deadline']:fail(503,'四智能体运行超过180秒时限','MODEL_TIMEOUT')
    with transaction() as db:
        current=one(db,'SELECT status FROM agent_runs WHERE id=:id',id=state['run_id'])
        if current['status']!='running':fail(409,'运行已取消或中断')
    from app.services.cloud_contracts import validate_output
    return validate_output(agent,response)


def authorized_sources(state):
    permitted=[]
    with transaction() as db:
        for source in state.get('sources',[]):
            resource=resource_access(db,state['actor'],source['resource_id'])
            if not resource['cloud_allowed']:continue
            if resource['current_version']!=source['version_id']:fail(409,'资料版本已变化')
            version=one(db,'SELECT body FROM resource_versions WHERE id=:v',v=source['version_id'])
            if not version:fail(409,'授权资料的版本已不存在')
            fragments=[]
            positions=[]
            source_remaining=2400
            for location in source.get('locations',[]):
                start,end=location.get('start'),location.get('end')
                if type(start) is not int or type(end) is not int or not 0<=start<end<=len(version['body']):
                    fail(409,'课程片段位置已失效，请重新生成规划','EVIDENCE_CHANGED')
                if source_remaining<=0:break
                fragment=version['body'][start:min(end,start+source_remaining)]
                if not fragment:continue
                fragments.append(fragment)
                positions.append({'skill':location['skill'],'start':start,'end':start+len(fragment)})
                source_remaining-=len(fragment)+3
            if not fragments:continue
            complete=(len(version['body'])<=8000 and any(p['start']==0 for p in positions))
            if complete:
                fragments=[version['body']]
                positions=[{'skill':skill,'start':0,'end':len(version['body'])} for skill in source['supported_skills']]
            excerpt=' … '.join(fragments)
            permitted.append({'resource_id':source['resource_id'],'version_id':source['version_id'],
                              'title':resource['title'][:200],'excerpt':excerpt,'locations':positions,
                              'supported_skills':source['supported_skills'],
                              'declared_skills':source.get('declared_skills',[]),
                              'coverage_scope':'complete_stored_document' if complete else 'partial_excerpt',
                              'stored_document_chars':len(version['body']),
                              'scope_note':'完整性仅指已保存的资料版本，不保证包含外部网页全文。片段中未出现不能推断原文不存在。',
                              'excerpt_sha256':hashlib.sha256(excerpt.encode()).hexdigest()})
    required={item['skill_id'] for item in state.get('execution',[])}
    supported={skill for source in permitted for skill in source['supported_skills']}
    if not required<=supported:
        from fastapi import HTTPException
        raise HTTPException(409,{'code':'CLOUD_PERMISSION_REQUIRED','message':'必要资料未授权云端发送，请调整资料授权或选择本地规划','missing_skills':sorted(required-supported)})
    # Keep at least one source for every required skill before adding optional evidence.
    selected=[]
    for skill in sorted(required):
        source=next(item for item in permitted if skill in item['supported_skills'])
        if source not in selected:selected.append(source)
    for source in permitted:
        if len(selected)>=8:break
        if source not in selected:selected.append(source)
    remaining=9000
    bounded=[]
    for source in selected:
        if len(source['excerpt'])>remaining:
            if any(skill in required and not any(skill in item['supported_skills'] for item in bounded) for skill in source['supported_skills']):
                fail(409,'课程片段超过云端发送上限，请缩小目标范围','EVIDENCE_LIMIT')
            continue
        bounded.append(source)
        remaining-=len(source['excerpt'])
    if not required<={skill for source in bounded for skill in source['supported_skills']}:
        fail(409,'课程片段超过云端发送上限，请缩小目标范围','EVIDENCE_LIMIT')
    return bounded


def diagnose(state):
    planning.diagnose(state)
    # The path builder excludes mastered goals and their prerequisites. Stop
    # before the first paid model call when that set is already empty.
    from app.services.learning import mastery
    with transaction() as db:
        current=mastery(db,state['actor']['id'])
    target=set(json.loads(state['goal']['skills']))
    for node in reversed(state['graph']):
        if node['id'] in target:target.update(node['prerequisites'])
    if not any(node['id'] in target and current.get(node['id'],{}).get('probability',.2)<.75 for node in state['graph']):
        planning.no_pending_work(state)
    # Resolve tasks and authorized evidence before paying for diagnosis.
    planning.build(state,record_step=False,cloud_preflight=True)
    if not state['execution']:
        fail(409,'当前没有需要安排的学习任务，请在产生新的学习需求或诊断证据后再生成路径','NO_PENDING_LEARNING_TASKS')
    sources=authorized_sources(state)
    permitted_versions={s['version_id'] for s in sources}
    state['reference_tasks']=[{'skill_id':task['skill_id'],'minutes':task['minutes'],
                               'estimated_remaining_activity':task['instructions'][:4000] if task.get('resume_proportion') and task.get('citations') and all(c['version_id'] in permitted_versions for c in task['citations']) else None,
                               'resume_proportion':task.get('resume_proportion'),'resume_policy':'若有累计进度，以estimated_remaining_activity为续学起点，只设计剩余练习和验证，不从头重读整份讲义。允许针对某个疑问简短回看；不要推断具体步骤已掌握或未掌握。将独立活动用1.、2.分段。','minutes_fixed':'remaining_minutes' in task,'prerequisites':task['prerequisites']}
                              for task in state['execution']]
    for ref, task in zip(state['reference_tasks'], state['execution']):
        if ref['estimated_remaining_activity']:
            problems = source_activity_issues([task], sources)
            if problems:
                # Carry progress forward without seeding every repair with a known-bad exercise.
                pending = ref['estimated_remaining_activity']
                ref['estimated_remaining_activity'] = (
                    '依据授权正文重新设计剩余练习与自检' if '阅读' not in pending else None
                )
                ref['resume_policy'] = '保留原有进度与剩余时长。旧练习存在教学错误，请从剩余练习和自检开始，依据本任务授权正文重新设计。'
                if (ref['estimated_remaining_activity']
                        and all(name in pending for name in ('by_value', 'by_ref', 'by_const_ref'))
                        and any('写入const引用' in issue for issue in problems)):
                    ref['estimated_remaining_activity'] = (
                        '1. 自编验证：在第一个源文件只定义 by_value(int x) 和 by_ref(int& x)，'
                        '函数体均为 x += 2;。从 int n=1 开始，依次调用 by_value(n)、by_ref(n)，'
                        '每次调用后输出 n，预期分别为 1、3。'
                        '2. 在另一个源文件只定义 void by_const_ref(const int& x) { x += 2; }，'
                        '单独编译该文件并记录定义处写入只读引用的错误。3. 自检：解释参数传递的差异。')
                ref['prior_activity_issues'] = problems
    state['preflight_built']=True
    response=agent_call(state,'GoalDiagnosisAgent',{
        'goal':state['goal']['title'],'skills':json.loads(state['goal']['skills']),
        'learner_evidence':state['learner_evidence'],'budget':state['budget_snapshot'],
        'instruction':'根据learner_evidence中已评分的诊断与当前掌握度分析目标和先修要求，区分首次诊断与后续变化。unknown表示缺少证据，不是已证实薄弱。学习时长不等于掌握度，不得推测未测技能、改写分数或BKT概率。返回summary字符串和risks字符串数组。'})
    if not isinstance(response.get('summary'),str):fail(503,'诊断智能体缺少summary','MODEL_SCHEMA_INVALID')
    state['cloud_outputs']={'diagnosis':response}
    planning.record_cloud_step(state,'GoalDiagnosisAgent','云端分析目标与先修要求')
    return state


def build_with_citation_feedback(state,payload):
    for attempt in range(2):
        response=agent_call(state,'PathBuildingAgent',payload)
        invalid=[task['skill_id'] for task in response['tasks'] if task['skill_id'] in payload['allowed_citation_version_ids_by_skill'] and not set(task['citation_version_ids'])<=set(payload['allowed_citation_version_ids_by_skill'][task['skill_id']])]
        if not invalid:return response
        feedback='、'.join(invalid)+'：引用版本不在该知识点允许的资料清单中。'
        if attempt:fail(503,'云端生成活动时修正引用后仍未通过：'+feedback+' 原路径保留。','MODEL_CITATION_INVALID')
        payload['citation_repair']=feedback+'请重新返回完整任务，只使用清单内能支持活动的版本；不能只替换编号而保留无依据的活动。'


def activity_sources(sources,tasks,review=False):
    """Keep excerpt and version together; annotate exactly which tasks may use each."""
    by_version={}
    for task in tasks:
        skill=task['skill_id']
        cited={c['version_id'] for c in task.get('citations',[])} if review else None
        for source in planning.preferred_sources_for_skill(sources,skill):
            version=source['version_id']
            if cited is not None and version not in cited:continue
            row=by_version.setdefault(version,{**source,'cite_for_skill_ids':[]})
            if skill not in row['cite_for_skill_ids']:row['cite_for_skill_ids'].append(skill)
    return list(by_version.values())


def build(state):
    if not state.get('preflight_built'):
        raise RuntimeError('Cloud planning requires a completed preflight')
    if not state['execution']:
        fail(409,'当前没有需要安排的学习任务，请在产生新的学习需求或诊断证据后再生成路径','NO_PENDING_LEARNING_TASKS')
    sources=authorized_sources(state)
    payload={
        'diagnosis':state['cloud_outputs']['diagnosis'],
        'learner_evidence':state['learner_evidence'],
        'budget_constraints':{
            **state['reference_budget'],
            'due_date':state['goal']['due_date'],
            'goal_weekly_minutes':state['goal']['weekly_minutes'],
            'daily_minutes':state['budget_snapshot']['daily_minutes'],
            'weekly_minutes':state['budget_snapshot']['weekly_minutes'],
            'study_weekdays':json.loads(state['budget_snapshot']['days']),
        },
        'required_task_count':len(state['execution']),
        'allowed_skill_ids':[task['skill_id'] for task in state['execution']],
        'allowed_citation_version_ids_by_skill':{task['skill_id']:[s['version_id'] for s in planning.preferred_sources_for_skill(sources,task['skill_id'])] for task in state['execution']},
        'citation_id_requirements':'citation_version_ids只能逐字复制对应技能允许的版本编号，在sources中找到cite_for_skill_ids含当前知识点的行，使用该行正文设计活动，直接复制同一行的version_id。禁止引用其他资料或使用网址、标题、资源编号。示例不完整时改选正文中完整的例子。',
        'source_fidelity_requirements':ACTIVITY_RULES,
        'excluded_mastered_or_out_of_scope_skills':[node['id'] for node in state['graph'] if node['id'] not in {task['skill_id'] for task in state['execution']}],
        'tasks':state['reference_tasks'],
        'sources':activity_sources(sources,state['execution']),'previous_review':state.get('cloud_issues',[]),
        'instruction':'参考方案存在 shortfall_minutes 时，在教学活动仍可完成的前提下按 schedulable_reference_minutes 安排总时长。结合截止日期、可学习日（0为周一）及个人和目标预算安排可执行活动。minutes_fixed 为真时保持给定分钟数。可调整任务共用 available_for_adjustable_tasks 分钟；请给必要的阅读、练习和测验留出时间。周预算为上限，实际排期由服务端核算。严格以输入tasks为唯一待学任务清单，输出tasks长度必须等于required_task_count，skill_id集合必须恰好等于allowed_skill_ids，每个技能只能有一个对象。多个阅读、练习、测评活动写在同一个instructions字符串中，不拆成多项。诊断summary仅供参考，禁止添加excluded_mastered_or_out_of_scope_skills中的技能，不重新安排已掌握的先修技能。若required_task_count为0返回空tasks。生成具体学习活动，保持全部skill_id各一次及先修顺序。返回tasks数组，每项含skill_id、正整数minutes、instructions字符串、citation_version_ids数组。只能引用所提供且支持对应技能的来源。返回reason字符串解释安排。'}
    response=build_with_citation_feedback(state,payload)
    proposed=response.get('tasks')
    expected={item['skill_id']:item for item in state['execution']}
    if not isinstance(proposed,list) or len(proposed)!=len(expected):fail(503,'路径智能体任务数量不符合要求','MODEL_SCHEMA_INVALID')
    ordered=[];seen=set()
    for entry in proposed:
        if not isinstance(entry,dict) or entry.get('skill_id') not in expected or entry['skill_id'] in seen:
            fail(503,'路径智能体返回未知或重复技能','MODEL_SCHEMA_INVALID')
        skill=entry['skill_id'];seen.add(skill)
        if type(entry.get('minutes')) is not int or not 1<=entry['minutes']<=1440 or not isinstance(entry.get('instructions'),str) or not entry['instructions'].strip():
            fail(503,'路径智能体任务内容或学时不合法','MODEL_SCHEMA_INVALID')
        citations=entry.get('citation_version_ids')
        allowed={source['version_id'] for source in planning.preferred_sources_for_skill(sources,skill)}
        if not isinstance(citations,list) or not citations or any(not isinstance(value,str) for value in citations) or not set(citations)<=allowed:
            fail(503,'路径智能体引用未经授权或不支持该技能的来源','MODEL_CITATION_INVALID')
        item=expected[skill]
        item.update(minutes=item.get('remaining_minutes',entry['minutes']),instructions=trim_completed_reading(state.get('reference_tasks',[]),skill,entry['instructions'])[:4000],
                    citations=[planning.citation_for_skill(source,skill) for source in state['sources'] if source['version_id'] in citations])
        ordered.append(item)
    state['execution']=ordered
    order={item['skill_id']:index for index,item in enumerate(ordered)}
    state['tasks'].sort(key=lambda item:order[item['skill_id']])
    state['remediations'].sort(key=lambda item:order[item['skill_id']])
    with transaction() as db:
        schedule=allocate(db,state['actor']['id'],sum(item['minutes'] for item in ordered),state['goal']['due_date'],state['goal']['id'],state['goal']['weekly_minutes']) if ordered else []
    remaining=[item['minutes'] for item in ordered]
    for item in ordered:item['dates']=[]
    for day in schedule:
        available=day['minutes']
        for index,item in enumerate(ordered):
            amount=min(available,remaining[index])
            if amount:item['dates'].append({'date':day['date'],'minutes':amount})
            available-=amount;remaining[index]-=amount
    from app.services.daily_activities import attach_daily_activities
    for scheduled_item in ordered:attach_daily_activities(scheduled_item)
    state['schedule']=schedule
    diff=state['diff']
    diff['after_schedule']=schedule
    final_minutes={item['skill_id']:item['minutes'] for item in ordered}
    diff['after_remaining_minutes']=sum(final_minutes.values())
    for row in diff['remaining_by_skill']:
        row['after']=final_minutes.get(row['skill_id'],0)
    state['cloud_outputs']['path']={'reason':str(response.get('reason',''))[:2000]}
    planning.record_cloud_step(state,'PathBuildingAgent','云端生成活动、学时与授权引用，经业务服务重新排期')
    return state


def trim_completed_reading(reference,skill,text):
    """For estimated continuations, trim only a standalone leading reading step."""
    import re
    ref=next((r for r in reference if r['skill_id']==skill),{})
    pending=ref.get('estimated_remaining_activity') or ''
    if not ref.get('resume_proportion') or not pending or '阅读' in pending:return text
    match=re.match(r'^\s*1[.、]\s*((?:阅读|重读|通读).*?)(?:[。；;]|\n)\s*2[.、]\s*(.+)$',text,re.S)
    if not match:return text
    if re.search('编写|手算|提交|作答|完成练习',match[1]):return text
    if not re.match('(?:接着|继续|完成|编写|手算|验证|练习|自检)',match[2]):return text
    rest=re.sub(r'(?<=[。；;])\s*(\d+)[.、]\s*',lambda m:str(max(1,int(m[1])-1))+'. ',match[2])
    return '1. '+rest


def resume_activity_issues(reference,tasks):
    import re
    by_skill={t['skill_id']:t for t in reference}
    issues=[]
    for task in tasks:
        ref=by_skill.get(task['skill_id'],{})
        pending=ref.get('estimated_remaining_activity') or ''
        if ref.get('resume_proportion') and pending and '阅读' not in pending and re.search(r'(?:通读|重读|阅读)[^。；\n]{0,60}(?:全文|整篇|整份)|从头(?:阅读|通读)',task.get('instructions','')):
            issues.append(task['skill_id']+'：剩余活动已进入练习或自检，请从未完成步骤开始，不再安排全文阅读；遇到困难可回看相关例子。')
    return issues


def review(state):
    with transaction() as db:state['rules']=validate(state,db)
    sources=authorized_sources(state)
    response=agent_call(state,'PlanReviewAgent',{
        'tasks':state['execution'],'rules':state['rules'],'sources':activity_sources(sources,state['execution'],review=True),
        'source_fidelity_requirements':ACTIVITY_RULES,
        'learner_evidence':state['learner_evidence'],
        'instruction':'审查教学顺序、具体活动与评估目标是否一致，并逐项对照sources的授权片段判断引用是否支持活动。若引入材料未覆盖的知识、外部资源或结论，必须拒绝并在issues列出具体技能和不受支持的内容。返回approved布尔值和issues字符串数组。每项活动只对照该项实际引用的版本。标注为自编验证输入的数值可与原例不同；检查方法、约定和知识是否有依据。未指定库或函数签名不等于要求学生使用某库或签名。教材已经明确给出的原练习按原练习审查；判断新增活动是否由正文支持。任务名是知识点标签，无需与教材标题逐字相同。assessment_source的题号来自已保存的诊断题库映射，由服务端VR-006核验，不要求教材正文出现题号，也不据此推断具体试题内容。仍须审查教学活动是否练到目标能力。硬规则失败必须拒绝。approved为true时issues必须为空。'})
    if type(response.get('approved')) is not bool or not isinstance(response.get('issues'),list):
        fail(503,'审查智能体输出不合法','MODEL_SCHEMA_INVALID')
    issues=[rule['reason'] for rule in state['rules'] if not rule['passed']]
    issues.extend(str(value)[:500] for value in response['issues'][:20])
    issues.extend(source_activity_issues(state['execution'],sources))
    issues.extend(resume_activity_issues(state.get('reference_tasks',[]),state['execution']))
    state['cloud_issues']=issues
    state['cloud_approved']=response['approved'] and not issues
    state['cloud_outputs']['review']=response
    state['cloud_repairs']=state.get('cloud_repairs',0)+(0 if state['cloud_approved'] else 1)
    if not state['cloud_approved'] and state['cloud_repairs']>3:
        fail(409,'路径在三次修复后仍未通过审查','PATH_REPAIR_EXHAUSTED')
    planning.record_cloud_step(state,'PlanReviewAgent','完成云端教学审查及确定性硬规则校验')
    return state


def finalize(state):
    authorized_sources(state)
    response=agent_call(state,'LearningReplanAgent',{
        'mode':state['mode'],'triggers':state['triggers'],'diff':state['diff'],
        'learner_evidence':state['learner_evidence'],
        'instruction':'依据learner_evidence的实际测验和diff解释哪些任务保留、移除或时长未变；学习记录完成不等于掌握。低于阈值但不再排课时给出可执行的巩固或复测建议，不得声称已掌握。解释风险和后续学习建议，返回reason字符串与recommendations字符串数组。不得改写已完成历史。'})
    if not isinstance(response.get('reason'),str):fail(503,'重规划智能体缺少原因解释','MODEL_SCHEMA_INVALID')
    state['cloud_outputs']['replan']=response
    return planning.save(state)


def tagged_step(agent,node):
    from fastapi import HTTPException
    def execute(state):
        try:return node(state)
        except HTTPException as exc:
            if isinstance(exc.detail,dict):exc.detail['cloud_failure_agent']=agent
            raise
    return execute


graph=StateGraph(planning.State)
for name,node in [('diagnose',diagnose),('build',build),('review',review),('save',finalize)]:graph.add_node(name,tagged_step({"diagnose":"GoalDiagnosisAgent","build":"PathBuildingAgent","review":"PlanReviewAgent","save":"LearningReplanAgent"}[name],node))
graph.set_entry_point('diagnose')
graph.add_edge('diagnose','build');graph.add_edge('build','review')
graph.add_conditional_edges('review',lambda state:'save' if state['cloud_approved'] else 'build',{'save':'save','build':'build'})
graph.add_edge('save',END)
workflow=graph.compile()

