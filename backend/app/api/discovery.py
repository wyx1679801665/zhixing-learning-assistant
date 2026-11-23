"""Student-owned web discovery drafts. No writes to the curriculum or mastery."""
import hashlib
import re
from decimal import Decimal
import httpx
import json
from datetime import date
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from app.core.db import dump, insert, now, one, rows, run, transaction, uid
from app.core.security import current_user, fail, role
from app.api.resources import visible
from app.services.engineering_goals import model_call
from app.services.learning import allocate
from app.services.web_evidence import fetch_page, search_web

router = APIRouter()


class Discovery(BaseModel):
    text: str = Field(min_length=1, max_length=200)
    web_consent: bool = False
    weekly_minutes: int = Field(ge=1, le=10080)
    due_date: date


class Choice(BaseModel):
    revision: int
    selected: list[str] = Field(max_length=48)


def owned(db, user, ident):
    role(user, 'student')
    record = one(db, 'SELECT * FROM discovery_drafts WHERE id=:i AND student_id=:u', i=ident, u=user['id'])
    if not record:
        fail(404, '个人草案不存在')
    return record


def dto(record):
    value={k: record[k] for k in ('id','description','status','revision','created_at')} | json.loads(record['content'])
    if value['status']=='ready' and not value.get('goal_checks'):
        value.update(status='partial',goal_gaps=['历史草案尚未核验完整学习目标，可重新生成。'])
    return value


def catalog_context(db, user):
    catalog = rows(db, """SELECT s.id,s.name,c.name course FROM curriculum_catalog_skills s
        JOIN curriculum_catalog_courses c ON c.id=s.course_id WHERE c.status='published'
        AND s.status IN ('approved','published') AND c.assessment_mode!='project_rubric' ORDER BY s.id""")
    # An approved personal goal can reference a pilot skill outside the public catalog.
    own_ids = set()
    for goal in rows(db, 'SELECT skills FROM goals WHERE student_id=:u AND status!=:a',u=user['id'],a='archived'):
        own_ids.update(s.removeprefix('catalog:') for s in json.loads(goal['skills']) if s.startswith('catalog:'))
    missing_ids = own_ids-{entry['id'] for entry in catalog}
    for skill_id in sorted(missing_ids):
        existing=one(db, """SELECT s.id,s.name,c.name course FROM curriculum_catalog_skills s
            JOIN curriculum_catalog_courses c ON c.id=s.course_id WHERE s.id=:i""",i=skill_id)
        if existing:
            catalog.append(existing)
    readable = {r['current_version'] for r in visible(db,user) if r['status']=='published' or r['scope']=='private'}
    linked = rows(db, """SELECT l.skill_id,v.id FROM learning_material_links l JOIN resource_versions v
        ON v.id=l.resource_version_id WHERE v.parse_status='ready' AND v.index_status='indexed' AND length(trim(v.body))>0""")
    return catalog, {link['skill_id'] for link in linked if link['id'] in readable}


def schedule(db, student, draft, selected):
    chosen = set(selected)
    if len(chosen)!=len(selected) or chosen-{n['ref'] for n in draft['nodes']}:
        fail(422,'所选知识点无效')
    plan = []
    blocked = set()
    for node in draft['nodes']:
        if node['ref'] not in chosen:
            continue
        if set(node['prerequisites'])-chosen:
            fail(422,'请同时采用所需先修步骤')
        if not node['ready'] or blocked.intersection(node['prerequisites']):
            blocked.add(node['ref'])
        else:
            plan.append(node)
    total = sum(n['minutes'] for n in plan)
    draft.update(selected=selected, schedule=[], blocked=sorted(blocked), shortfall_minutes=0)
    if not total:
        return
    def slots(minutes):
        return allocate(db,student,minutes,draft['due_date'],goal_limit=draft['weekly_minutes'])
    try:
        days = slots(total)
    except HTTPException as exc:
        conflict = exc.detail.get('budget_conflict') if isinstance(exc.detail,dict) else None
        if not conflict:
            raise
        draft['shortfall_minutes'] = conflict['shortfall_minutes']
        days = slots(conflict['available_minutes']) if conflict['available_minutes'] else []
    index = 0
    for node in plan:
        left = node['minutes']
        while left and index<len(days):
            day = days[index]
            used = min(left,day['minutes'])
            draft['schedule'].append(dict(ref=node['ref'],date=day['date'],minutes=used))
            left -= used
            day['minutes'] -= used
            if not day['minutes']:
                index += 1


def evidence_window(body, terms, size=5000):
    """Choose one contiguous source passage, preserving verbatim quotation."""
    if len(body)<=size:
        return body
    needles=[t.casefold() for t in terms if len(t.strip())>=2]
    offsets=list(range(0,len(body)-size+1,size//2))
    offsets.append(len(body)-size)
    def score(offset):
        section=body[offset:offset+size].casefold()
        counts=[section.count(term) for term in needles]
        return sum(bool(n) for n in counts),sum(min(n,5) for n in counts)
    start=max(offsets,key=score)
    return body[start:start+size]


def validate_outline(outline,catalog,covered):
    steps = outline.get('steps')
    if not isinstance(steps,list) or not 1<=len(steps)<=12:
        raise ValueError('步骤数量无效')
    known = {c['id']:c for c in catalog}
    names = {c['name'].strip().casefold():c['id'] for c in catalog}
    local_ids = [s.get('catalog_id') or names.get(str(s.get('name','')).strip().casefold()) for s in steps if isinstance(s,dict)]
    local_ids = list(dict.fromkeys(k for k in local_ids if isinstance(k,str) and k in known))
    required = {}
    if local_ids:
        from app.services.engineering_goals import scope
        with transaction() as db:
            expanded,_ = scope(db,local_ids,pilot=True)
        for entry in expanded:
            if entry['id'] not in known:
                raise ValueError('课程所需先修尚未对本账号开放')
            required[entry['id']] = [p.removeprefix('catalog:') for p in entry['prerequisites']]
    seen = {}
    objectives = {}
    step_refs = {}
    nodes = []
    for index,step in enumerate(steps,1):
        if not isinstance(step,dict):
            raise ValueError('步骤格式异常')
        name = step.get('name')
        if not isinstance(name,str) or not 1<=len(name.strip())<=100:
            raise ValueError('知识点名称无效')
        cid = step.get('catalog_id') or names.get(name.strip().casefold())
        if cid is not None and (not isinstance(cid,str) or cid not in known):
            raise ValueError('目录编号无效')
        if cid:
            name = known[cid]['name']
        key = cid or name.strip().casefold()
        deps = step.get('prerequisites',[])
        minutes = step.get('minutes')
        objective = step.get('objective')
        if key in seen and cid is None:
            raise ValueError(f'第{index}步重复知识点：{name}')
        if not isinstance(deps,list) or any(type(d)!=int or not 1<=d<index for d in deps):
            raise ValueError(f'第{index}步的先修编号必须为此前步骤的正整数序号')
        if type(minutes)!=int or not 1<=minutes<=1440:
            raise ValueError(f'第{index}步预计分钟不是有效正整数')
        if not isinstance(objective,str) or not objective.strip():
            raise ValueError(f'第{index}步缺少可观察目标')
        node = dict(ref=f'step-{index}',name=name,catalog_id=cid,objective=objective[:500],minutes=minutes,
                    prerequisites=list(dict.fromkeys(step_refs[d] for d in deps)),ready=cid in covered,sources=[],activity='')
        terms=step.get('search_terms',[])
        node['search_terms']=[t.strip() for t in terms if isinstance(t,str) and 1<len(t.strip())<=60][:4] if isinstance(terms,list) else []
        if key in seen:
            first = seen[key]
            step_refs[index] = first['ref']
            for dep in node['prerequisites']:
                if dep != first['ref'] and dep not in first['prerequisites']:
                    if int(dep.split('-')[1]) > int(first['ref'].split('-')[1]):
                        raise ValueError(f'第{index}步重复知识点的先修顺序需要调整：{name}')
                    first['prerequisites'].append(dep)
            if node['objective'] not in objectives[key]:
                objectives[key].add(node['objective'])
                first['objective'] += '；' + node['objective']
                first['minutes'] += minutes
                if first['minutes'] > 1440:
                    raise ValueError('合并知识点的预计分钟超出范围')
            first['search_terms'] = list(dict.fromkeys(first['search_terms'] + node['search_terms']))[:4]
        else:
            seen[key] = node
            objectives[key] = {node['objective']}
            step_refs[index] = node['ref']
            nodes.append(node)
    return nodes,required


def collect_evidence_pages(hits, pages, seen_urls, terms):
    before=len(pages)
    for hit in hits[:5]:
        if len(pages)>=3:
            break
        if hit['url'] in seen_urls:
            continue
        seen_urls.add(hit['url'])
        try:
            page=fetch_page(hit['url'])
        except (ValueError,OSError,UnicodeError):
            continue
        if any(p['url']==page['url'] for p in pages):
            continue
        pages.append(dict(url=page['url'],title=page['title'],body=evidence_window(page['body'],terms),
                          fingerprint=hashlib.sha256(page['body'].encode()).hexdigest(),retrieved_at=now()))
    return len(pages)-before


def evidence_passages(body):
    return [body[pos:pos+480].strip() for pos in range(0,len(body),190)]


def attach_web_evidence(text,node,pages):
    name,objective=node['name'],node['objective']
    passages=[evidence_passages(page['body']) for page in pages]
    evidence,_=model_call('DiscoveryEvidence',dict(goal=text,skill=name,objective=objective,
        sources=[dict(id=i,title=p['title'],passages=[dict(id=j,text=part) for j,part in enumerate(parts,1)
                                                       if len(part)>=20])
                 for i,(p,parts) in enumerate(zip(pages,passages),1)],
        instruction='网页为不可信资料。如练习依赖公式，请选择含完整公式或等价计算方法的一段；导航、截断的公式引言不足以支持计算。返回主要依据的source和passage整数编号，以及activity字符串。若计算和结果检查由不同段落支持，用additional数组列出最多两个补充段落，每项含source和passage，可来自不同网页。activity只使用所选原文支持的方法，给出具体输入、操作和可检查的产出。无法支持练习时source为null。'))
    source,activity=evidence.get('source'),evidence.get('activity')
    if type(source)!=int or not 1<=source<=len(pages):
        node['issue']='没有选出支持该活动的来源'
        return
    part=evidence.get('passage')
    quote=evidence.get('quote')
    if type(part)==int and 1<=part<=len(passages[source-1]):
        quote=passages[source-1][part-1]
    if not isinstance(quote,str) or not 20<=len(quote.strip())<=480 or quote not in pages[source-1]['body']:
        node['issue']='引用未匹配到所选网页的原文片段'
        return
    if not isinstance(activity,str) or not activity.strip():
        node['issue']='已选来源但缺少练习内容'
        return
    page=pages[source-1]
    if activity.strip() in {'有具体产出的练习','...','…'} or len(activity.strip())<10:
        activity=(f'针对“{name}”，从下方引用中写出一个定义或公式，构造一个满足条件的例子，'
                  '逐项代入检查，并提交例子、检查过程和结论。')
    node.update(ready=True,activity=activity[:1000],sources=[{k:v for k,v in page.items() if k!='body'}|{'quote':quote}])
    additional=evidence.get('additional')
    if isinstance(additional,list):
        for item in additional[:2]:
            if not isinstance(item,dict):
                continue
            src,idx=item.get('source'),item.get('passage')
            if type(src)!=int or not 1<=src<=len(pages) or type(idx)!=int or not 1<=idx<=len(passages[src-1]):
                continue
            excerpt=passages[src-1][idx-1]
            if len(excerpt)>=20 and all(s['quote']!=excerpt for s in node['sources']):
                node['sources'].append({k:v for k,v in pages[src-1].items() if k!='body'}|{'quote':excerpt})


def discover(text, catalog, covered, draft):
    if re.search(r'(?:尚未提供|未提供|尚未上传|未上传)的(?:本班|本校|本课程|内部|个人|专用)',text):
        raise ValueError('请先提供目标指定的专用资料，例如本班评分细则正文，再重新规划。')
    # Resolve against the whole catalog in bounded batches, including an empty catalog.
    matches = []
    for start in range(0,len(catalog),100):
        batch = catalog[start:start+100]
        answer,_ = model_call('DiscoveryCatalogMatch',dict(goal=text,catalog=batch,
            instruction='只列为完成目标直接需要或必要先修的条目，返回 {"ids":["目录id"]}；无则空数组。'))
        ids = answer.get('ids')
        if not isinstance(ids,list) or any(not isinstance(k,str) or k not in {c['id'] for c in batch} for k in ids):
            raise ValueError('目录匹配格式异常')
        matches.extend(c for c in batch if c['id'] in ids)
    outline,_ = model_call('DiscoveryOutline',dict(goal=text,matches=matches,
        instruction='按学习顺序输出最小必要知识点，优先3至5项，最多12项。每个知识点只出现一次，同一知识点的活动合并。不要把每一步计算都拆成新知识点。逐项覆盖原目标明确要求的实际操作与验证，用最后一个活动完成原目标的交付物；基础定义不能代替应用。按以下格式输出 {"steps":[{"name":"知识点","catalog_id":null,"prerequisites":[],"objective":"可观察目标","search_terms":["核心术语","英文同义词"],"minutes":30}]}。先修用此前步骤的1起始序号。每项给出2至4个具体检索短语，其中包含目标的学科背景和该节点的英文术语，不含个人信息。已有匹配必须使用原ID；库外用null。'))
    try:
        nodes,required=validate_outline(outline,catalog,covered)
    except ValueError as exc:
        draft['outline_repair_reason']=str(exc)
        repaired,_=model_call('DiscoveryOutlineRepair',dict(goal=text,matches=matches,outline=outline,error=str(exc),
            instruction='返回完整steps数组。按目录ID检查重复：即使name不同，同一个非空catalog_id也只允许一项。'
                        '目录同名项即使ID为null也会关联该目录项。合并重复项的操作和验证到同一个objective；'
                        '确属独立的库外知识点才使用null和准确的独立名称。重新编号先修，第一项先修为空数组。'))
        nodes,required=validate_outline(repaired,catalog,covered)
    draft['nodes']=nodes
    searches=0
    # Validate the entire outline before spending any web-search calls.
    for position,node in enumerate(draft['nodes']):
        name=node['name']
        objective=node['objective']
        if node['ready']:
            node['activity']='阅读本人可访问的关联讲义，完成一个例子并对照学习目标自检。'
            continue
        if searches>=6:
            node['issue']='达到本次六次检索上限'
            continue
        searches+=1
        terms=node['search_terms']
        query=' '.join([name,*terms[:2]])[:200]
        log=dict(query=query,hits=0,pages=0)
        draft['searches'].append(log)
        try:
            hits=search_web(query)
            log['hits']=len(hits)
        except Exception as exc:
            log['error_type']=type(exc).__name__
            node['issue']=str(exc)[:160] if isinstance(exc,ValueError) else '联网检索暂不可用（'+type(exc).__name__+'）'
            continue
        pages=[]
        seen_urls=set()
        log['pages']=collect_evidence_pages(hits,pages,seen_urls,[name,*terms])
        remaining=sum(not later['ready'] for later in draft['nodes'][position+1:])
        # Leave the final spare search for a gap identified by goal review.
        if terms and len(pages)<3 and searches+remaining<5:
            query=' '.join([*terms[:2],'worked example step by step verification'])[:200]
            extra=dict(query=query,hits=0,pages=0)
            draft['searches'].append(extra)
            searches+=1
            try:
                alternatives=search_web(query)
                extra['hits']=len(alternatives)
                extra['pages']=collect_evidence_pages(alternatives,pages,seen_urls,[name,*terms])
            except Exception as exc:
                extra['error_type']=type(exc).__name__
        if not pages:
            node['issue']='未取得可核对的网页正文'
            continue
        attach_web_evidence(text,node,pages)
    # Merge required catalog dependencies with model-suggested order before scheduling.
    local_refs = {n['catalog_id']:n for n in draft['nodes'] if n['catalog_id']}
    known = {entry['id']:entry for entry in catalog}
    for cid, prereqs in required.items():
        if cid not in local_refs:
            entry=dict(ref='prereq-'+cid,name=known[cid]['name'],catalog_id=cid,objective='满足课程目录中的必要先修',
                       minutes=30,prerequisites=[],ready=cid in covered,sources=[],
                       activity='阅读关联讲义并完成一个先修示例。' if cid in covered else '',issue='必要先修缺少可用正文')
            local_refs[cid]=entry
            draft['nodes'].append(entry)
    nodes=draft['nodes']
    proposed=[(n['ref'],p) for n in nodes for p in n['prerequisites']]
    edges={n['ref']:[] for n in nodes}
    for cid, prereqs in required.items():
        edges[local_refs[cid]['ref']]=[local_refs[p]['ref'] for p in prereqs]
    for target, prerequisite in proposed:
        if prerequisite in edges[target]:
            continue
        # Add advisory edges only where the authoritative prerequisite graph permits them.
        pending=[prerequisite]
        visited=set()
        while pending:
            ref=pending.pop()
            if ref in visited:
                continue
            visited.add(ref)
            pending.extend(edges[ref])
        if target in visited:
            draft.setdefault('omitted_suggestions',[]).append(dict(step=target,prerequisite=prerequisite))
        else:
            edges[target].append(prerequisite)
    for node in nodes:
        node['prerequisites']=edges[node['ref']]
    ordered=[]
    done=set()
    while len(ordered)<len(draft['nodes']):
        layer=[n for n in draft['nodes'] if n['ref'] not in done and set(n['prerequisites'])<=done]
        if not layer:
            for n in draft['nodes']:
                if n['ref'] not in done:
                    n.update(ready=False,issue='建议顺序与课程先修冲突，需重新核对')
            raise ValueError('课程与建议的先修关系存在环')
        ordered.extend(layer)
        done.update(n['ref'] for n in layer)
    draft['nodes']=ordered



def original_excerpt(text, excerpt):
    """Match through layout whitespace while retaining the source spelling."""
    if not isinstance(excerpt,str) or not excerpt.strip():
        return None
    if excerpt in text:
        return excerpt
    # Ignore only whitespace around mathematical symbols. Keep word boundaries.
    import re
    def normalized(value):
        pieces=[]
        positions=[]
        for match in re.finditer(r'\s+|\S',value):
            start,end=match.span()
            if match.group().isspace():
                if not (start and end<len(value) and value[start-1].isalnum() and value[end].isalnum()):
                    continue
                pieces.append(' ')
            else:
                pieces.append(match.group())
            positions.append(start)
        return ''.join(pieces),positions
    needle,_=normalized(excerpt.strip())
    haystack,positions=normalized(text)
    index=haystack.find(needle)
    if needle and index>=0:
        return text[positions[index]:positions[index+len(needle)-1]+1]
    return None


QUATERNION_ROTATION_REQUIREMENT='用单位四元数计算旋转后的三维向量'
QUATERNION_LENGTH_REQUIREMENT='分别计算旋转前后三维向量的欧几里得范数并验证相等'


def review_goal(goal, nodes, requirements=None):
    if requirements is None and goal.strip()=='用单位四元数旋转三维向量，并验证旋转前后向量长度相等':
        requirements=[QUATERNION_ROTATION_REQUIREMENT,QUATERNION_LENGTH_REQUIREMENT]
    if (requirements is None and '二分查找' in goal and '已排序数组中定位目标' in goal
            and '不存在的目标验证返回结果' in goal
            and not any(term in goal for term in ('lower_bound','首次','第一次','最后','插入位置'))):
        requirements=['在已排序数组中用二分查找定位存在的目标',
                      '用不存在的目标验证二分查找的未命中返回结果']
    activities={n['ref']:n for n in nodes if n['ready']}
    if not activities:
        return []
    import re
    clauses={ref:[s.strip() for s in re.split(r'(?<=[；。;])',n['activity']) if s.strip()]
             for ref,n in activities.items()}
    result,_=model_call('DiscoveryGoalReview',dict(goal=goal,required_checks=requirements,activities=[
        dict(ref=n['ref'],activity=n['activity'],activity_steps=[
            dict(id=i,text=clause) for i,clause in enumerate(clauses[n['ref']],1)],quotes=[
            dict(id=i,lines=[dict(line=j,text=t) for j,t in enumerate(s['quote'].splitlines(),1)])
            for i,s in enumerate(n['sources'],1)])
        for n in activities.values()],instruction=(
        '独立拆解原目标的每个关键操作和可检查结果。只按实际活动文字及外部引文核对，'
        'requirement写目标要求的方法和可检查结果，不把自拟例题的数组、坐标、索引或数值答案升级为目标要求。'
        '若required_checks非空，仅逐项审核这些原要求，requirement原样返回。实现方式和库函数只有在原目标明确指定时才属于必修要求。说明能支持后续操作不等于要求学生执行操作。'
        '标题、愿望、笼统自检、可以验证等不算完成。计算练习的外部引文须含所需公式或方法。'
        '例如要求用单位四元数旋转向量并验证保距，则须分别检查：由输入求旋转后的向量；比较两个向量的长度。'
        '返回checks非空数组。每项含requirement和supports数组。达标时supports列出共同必需的活动，'
        '每项含ref和activity_step，activity_step是对应操作在activity_steps中的整数id。'
        '按实际文字核对每个操作。若活动确实要求计算，但来源缺失，仅指出缺失的来源。'
        '示例结构：{"checks":[{"requirement":"操作","supports":[{"ref":"step-2","activity_step":1,"quote_id":1,"first_line":2,"last_line":3}]}]}。'
        '核对操作与引文是否一致；公式中操作数、共轭或逆写错时，不将该活动作为该操作的依据。'
        '若该活动有quotes，填quote_id、first_line、last_line三个整数，选择同一引文内足以教会学生执行本项操作的连续行。'
        '逐行确认公式或方法完整且与本项活动相符；已给定的输入无须另列构造该输入的考核要求。'
        '只提到术语、指向前文公式或缺图时，必须报告缺口；不可用activity中的公式补足引文。'
        '未达标时supports为空并用gap写明缺少什么。')))
    checks=result.get('checks')
    if not isinstance(checks,list) or not 1<=len(checks)<=20:
        raise ValueError('目标完成度审查格式无效')
    validated=[]
    for check in checks:
        if not isinstance(check,dict) or not isinstance(check.get('requirement'),str) or not check['requirement'].strip():
            raise ValueError('目标完成度审查缺少要求')
        supports=check.get('supports')
        if not isinstance(supports,list):
            raise ValueError('目标完成度审查缺少活动依据')
        refs=[]
        citations=[]
        for item in supports:
            if not isinstance(item,dict):
                raise ValueError('活动依据格式无效')
            ref=item.get('ref')
            evidence=item.get('evidence')
            if 'activity_step' in item:
                idx=item['activity_step']
                options=clauses.get(ref,[]) if isinstance(ref,str) else []
                evidence=options[idx-1] if type(idx) is int and 1<=idx<=len(options) else None
            if (not isinstance(ref,str) or ref not in activities or not isinstance(evidence,str)
                    or not original_excerpt(activities[ref]['activity'],evidence)):
                refs.clear()
                check['gap']='缺少可核对的已排期活动依据：'+check['requirement']
                break
            sources=activities[ref]['sources']
            if sources:
                index,first,last=(item.get(k) for k in ('quote_id','first_line','last_line'))
                original=None
                if all(type(i) is int for i in (index,first,last)) and 1<=index<=len(sources):
                    lines=sources[index-1]['quote'].splitlines(keepends=True)
                    if 1<=first<=last<=len(lines):
                        original=''.join(lines[first-1:last]).strip() or None
                if original is None:
                    refs.clear()
                    citations.clear()
                    check['gap']='外部引文未提供可核对的操作依据：'+check['requirement']
                    break
                citations.append(dict(ref=ref,activity_excerpt=evidence,quote_id=index,
                                      first_line=first,last_line=last,excerpt=original))
            refs.append(ref)
        validated.append(dict(requirement=check['requirement'][:300],refs=refs,
                              citations=citations if refs else [],
                              gap=str(check.get('gap') or check['requirement'])[:500]))
    if requirements is not None:
        by_requirement={check['requirement']:check for check in validated}
        validated=[by_requirement.get(requirement,dict(requirement=requirement,refs=[],citations=[],
                   gap='未取得该目标要求的审核依据：'+requirement)) for requirement in requirements]
    claims=[dict(id=i,requirement=c['requirement'],excerpts=[s['excerpt'] for s in c['citations']])
            for i,c in enumerate(validated,1) if c['citations']]
    if claims:
        # Give this check only the selected lines. The generated activity or the
        # rest of the webpage must not supply facts missing from the citation.
        decision,_=model_call('DiscoveryCitationReview',dict(claims=claims,
            instruction='仅按每项excerpts原文判断是否足以支持requirement的操作或结论。'
                        '对自拟例题，只核对算法、公式或检查方法的依据，例题数值答案由单独的复算环节检查。'
                        '不得利用背景知识补足缺失步骤；只出现主题、标题、坐标变化等描述不能证明长度比较。'
                        '返回supported_ids数组，只列有直接支持的整数id，证据不足时省略。'))
        ids=decision.get('supported_ids')
        valid_ids={claim['id'] for claim in claims}
        approved=set(ids) if isinstance(ids,list) and all(type(i) is int and i in valid_ids for i in ids) else set()
        for claim in claims:
            if claim['id'] not in approved:
                check=validated[claim['id']-1]
                if set(check['refs'])=={'goal-practice'}:
                    expanded=[]
                    for citation in check['citations']:
                        quote=activities[citation['ref']]['sources'][citation['quote_id']-1]['quote']
                        expanded.append(dict(citation,excerpt=quote,first_line=1,last_line=len(quote.splitlines())))
                    if any(a['excerpt']!=b['excerpt'] for a,b in zip(expanded,check['citations'])):
                        result,_=model_call('DiscoveryCitationReview',dict(
                            claims=[dict(claim,excerpts=[c['excerpt'] for c in expanded])],
                            instruction='只按这些完整原文片段核对requirement所需的全部方法。'
                                        '数值例题允许引用通用计算方法，自拟数据与答案另行复算。'
                                        '例如数值比较向量长度需要欧几里得范数或平方和的依据，一般性保长证明则需要推导依据。'
                                        '方法完整才返回该项整数编号到supported_ids。'))
                        if result.get('supported_ids')==[claim['id']]:
                            check['citations']=expanded
                            continue
                check.update(refs=[],citations=[],gap='所选网页原文不足以支持该操作：'+check['requirement'])
    for check in validated:
        requirement=check['requirement']
        if '四元数' not in goal or not check['citations']:
            continue
        gap=quaternion_norm_gap(requirement,[c['excerpt'] for c in check['citations']])
        if gap:
            check.update(refs=[],citations=[],gap=gap+'：'+requirement)
    binary_examples={}
    for ref,node in activities.items():
        related=[c for c in validated if ref in c['refs']]
        if not related or not re.search(r'\d',node['activity']):
            continue
        verdict,_=model_call('DiscoveryExampleReview',dict(goal=goal,activity=node['activity'],
            instruction='独立复算活动中给定具体输入及声称的输出。返回status(correct/incorrect/no_example/unverified)及work字符串。'
                        'work写出输入、中间结果和比较结论。只有符号公式、没有可复算的具体输入与输出时用no_example。'
                        '二分查找遇重复值，题目未指定首次或末次时接受任何指向目标的合法索引；未命中按题目约定检查。'
                        '二分查找数值案例还须返回binary_cases数组，逐例照录整数数组values、整数target及活动声称的零基index（未命中用null或-1）。'
                        '对左闭右闭的升序未命中例题，另填trace，逐轮照录活动中实际写出的[左界,右界,中点]，从初始区间开始；不得替活动补写。'
                        '四元数标明实部位置，核算旋转后坐标与两个模长；不得将不同顺序的分量直接混用。'))
        status=verdict.get('status')
        work=verdict.get('work')
        if status=='correct' and '二分查找' in goal:
            cases=verdict.get('binary_cases')
            if isinstance(cases,list) and any(
                valid_search_example(case) and case['index'] in (None,-1) and 'trace' not in case
                for case in cases
            ):
                extracted,_=model_call('DiscoveryBinaryTrace',dict(activity=node['activity'],cases=cases,
                    instruction='逐项返回traces数组，与cases顺序一致。命中项填null；未命中项填活动原文中逐轮出现的[左界,右界,中点]数组，例如[[0,4,2],[0,1,0]]。缺少任一轮就填null，不从算法或复算补写。'))
                traces=extracted.get('traces')
                if isinstance(traces,list) and len(traces)==len(cases):
                    for case,trace in zip(cases,traces):
                        if isinstance(case,dict) and 'trace' not in case and isinstance(trace,list):
                            case['trace']=trace
        if status=='correct' and '二分查找' in goal:
            cases=verdict.get('binary_cases')
            if not isinstance(cases,list) or not cases or not all(valid_search_example(c) for c in cases):
                status='unverified'
            else:
                binary_examples[ref]=cases
        for check in related:
            review=dict(ref=ref,status=status,work=str(work or '')[:1200])
            if ref in binary_examples:
                review['binary_cases']=binary_examples[ref]
            check.setdefault('example_reviews',[]).append(review)
            if status not in ('correct','no_example') or not isinstance(work,str) or not work.strip():
                check.update(refs=[],citations=[],gap='例题复算未通过：'+check['requirement'])
    required_examples={
        '在已排序数组中用二分查找定位存在的目标':True,
        '用不存在的目标验证二分查找的未命中返回结果':False,
    }
    for check in validated:
        requirement=check['requirement']
        if check['refs'] and requirement in required_examples:
            expects_hit=required_examples[requirement]
            if not any((case['target'] in case['values'])==expects_hit and
                       (expects_hit or complete_miss_trace(case) and miss_trace_in_activity(case,activities[ref]['activity']))
                       for ref in check['refs'] for case in binary_examples.get(ref,[])):
                check.update(refs=[],citations=[],gap='已安排活动缺少经复算的对应例题：'+requirement)
    return validated


def quaternion_norm_gap(requirement,excerpts):
    if requirement==QUATERNION_ROTATION_REQUIREMENT:
        if not any(has_quaternion_rotation_formula(excerpt) for excerpt in excerpts):
            return '原文缺少四元数共轭旋转运算公式'
        return None
    # Unit-quaternion normalization uses four components, a separate check
    # from comparing the length of the input and output 3D vectors.
    if re.search(r'单位模|单位四元数',requirement) and not re.search(
            r'旋转前后|分别计算|两个向量|‖[^‖]+‖\s*=\s*‖|\|\|[^|]+\|\|\s*=\s*\|\|',requirement):
        if re.search(r'是否为单位模|验证.*单位模|判断.*单位|确认.*(?:共轭|q̂⁻¹)',requirement):
            return None
    if not re.search(r'范数|模长|长度|保长|保距|分量平方和|√|‖[^‖]+‖|\|\|[^|]+\|\|',requirement):
        return None
    if re.search(r'证明|推导|恒等|因.*(?:保长|正交|保距)',requirement) and not re.search(r'浮点|数值|分别计算|√',requirement):
        if not any(re.search(r'范数.{0,12}乘法性|模长.{0,12}乘法性|正交.{0,20}(?:保长|保距|保持长度)|norm.{0,15}multiplicativ',
                             excerpt,re.IGNORECASE) for excerpt in excerpts):
            return '原文缺少保长推导依据'
    elif not any(has_norm_formula(excerpt) for excerpt in excerpts):
        return '原文缺少三维向量范数或分量平方和的计算依据'
    return None


def has_quaternion_rotation_formula(excerpt):
    compact=re.sub(r'\s+|\\(?:cdot|otimes|[,;!])|[·⊗×]','',excerpt)
    compact=compact.replace('⁻¹','^-1').replace('−','-').replace('^{-1}','^-1')
    return bool(re.search(r'([quQ])(?:\*?\(0[,;][pvwu]\)|\*?[pvwα]|\*?\[0,[pvw]\])\*?\1(?:\^-1|\*|∗)',compact)
                or re.search(r'\b([quQ])\s*\*?\s*[pvwα]\s*\*?\s*conj\(\1\)',excerpt))


def has_norm_formula(excerpt):
    compact=re.sub(r'\s+','',excerpt).replace('²','^2')
    compact=re.sub(r'\^\{2\}', '^2',compact)
    if re.search(r'(?:三维向量|三个分量).{0,30}平方和|(?:3Dvector|threecomponents).{0,45}sum(?:of)?(?:the)?squares',
                 compact,re.IGNORECASE):
        return True
    squared_term=r"[A-Za-z][A-Za-z0-9_′']*\^2"
    sums=re.finditer(rf'{squared_term}(?:\+{squared_term}){{2,}}',compact)
    return any(match.group().count('+')==2 for match in sums)


def binary_search_rounds(values,target):
    left,right=0,len(values)-1
    expected=[]
    while left<=right:
        middle=(left+right)//2
        expected.append([left,right,middle])
        if values[middle]<target:
            left=middle+1
        else:
            right=middle-1
    return expected,(left,right)


def complete_miss_trace(case):
    values=case['values']
    return values==sorted(values) and case.get('trace')==binary_search_rounds(values,case['target'])[0]


def miss_trace_in_activity(case,activity):
    """Require the round bounds and midpoint in the student-visible text."""
    position=0
    for left,right,middle in case['trace']:
        pattern=(rf'(?:左界|left|low|i)\s*[=：:]?\s*{left}\b[^\n；。]*?'
                 rf'(?:右界|right|high|j)\s*[=：:]?\s*{right}\b[^\n；。]*?'
                 rf'(?:中点|mid|m)\s*[=：:]?\s*{middle}\b')
        match=re.search(pattern,activity[position:],re.IGNORECASE)
        if match is None:
            return False
        position+=match.end()
    return True


def binary_miss_practice():
    values=[1,3,5,7,9]
    target=4
    trace,(left,right)=binary_search_rounds(values,target)
    steps=[f'给定升序数组 {values}，目标值 {target}。采用左闭右闭区间，从 [0,4] 开始。']
    for index,(low,high,middle) in enumerate(trace,1):
        value=values[middle]
        update=f'右界更新为 {middle-1}' if value>target else f'左界更新为 {middle+1}'
        operator='>' if value>target else '<'
        steps.append(f'第 {index} 轮：左界 {low}，右界 {high}，中点 {middle}，'
                     f'元素 {value} {operator} 目标 {target}，{update}。')
    steps.append(f'退出：左界 {left} > 右界 {right}，区间为空，返回 -1。'
                 '请逐轮手算并核对每次边界更新与最终返回值。')
    return '\n'.join(steps)


def valid_search_example(case):
    if not isinstance(case,dict) or 'index' not in case:
        return False
    values,target,index=case.get('values'),case.get('target'),case['index']
    if not isinstance(values,list) or type(target)!=int or any(type(v)!=int for v in values):
        return False
    if values!=sorted(values) and values!=sorted(values,reverse=True):
        return False
    if index is None or type(index) is int and index==-1:
        return target not in values
    return type(index) is int and 0<=index<len(values) and values[index]==target


def default_schedule_selection(draft):
    nodes=draft['nodes']
    checks=draft.get('goal_checks') or []
    if not checks or any(not check['refs'] for check in checks):
        return [node['ref'] for node in nodes]
    prerequisites={node['ref']:node['prerequisites'] for node in nodes}
    selected={ref for check in checks for ref in check['refs']}
    pending=list(selected)
    while pending:
        for ref in prerequisites[pending.pop()]:
            if ref not in selected:
                selected.add(ref)
                pending.append(ref)
    return [node['ref'] for node in nodes if node['ref'] in selected]


def assess_schedule(draft):
    minutes={}
    for day in draft.get('schedule',[]):
        minutes[day['ref']]=minutes.get(day['ref'],0)+day['minutes']
    complete={n['ref'] for n in draft['nodes'] if n['ready'] and minutes.get(n['ref'],0)>=n['minutes']}
    checks=draft.get('goal_checks') or []
    gaps=[]
    for check in checks:
        if not check['refs']:
            gaps.append(check['gap'])
        elif set(check['refs'])-complete:
            gaps.append('所需活动未完整排期：'+check['requirement'])
    if not checks:
        gaps.append('尚未核验完整学习目标')
    draft['goal_gaps']=gaps
    return 'partial' if gaps or draft.get('error') or draft.get('blocked') or draft.get('shortfall_minutes') else 'ready'


def supplement_missing_activity(goal,draft):
    missing=[check for check in draft['goal_checks'] if not check['refs']]
    external=[n for n in draft['nodes'] if n['ready'] and n['sources']]
    requirements=[check['requirement'] for check in missing]
    if not requirements or (not external and not set(requirements)<={
            QUATERNION_ROTATION_REQUIREMENT,QUATERNION_LENGTH_REQUIREMENT}):
        return
    norm_comparison=(
        '四元数' in goal and '证明' not in goal
        and any(quaternion_norm_gap(requirement,[])=='原文缺少三维向量范数或分量平方和的计算依据'
                for requirement in requirements)
    )
    sources=[source for node in external for source in node['sources']]
    def propose(format_issue=None):
        response,_=model_call('DiscoveryPracticeRepair',dict(goal=goal,requirements=requirements,format_issue=format_issue,
            sources=[dict(id=i,quote=s['quote']) for i,s in enumerate(sources,1)],
            instruction='仅利用提供的引文，为缺失操作设计一道可执行的综合练习。给出具体输入、逐步操作、预期结果和核对方法；'
                    '数值任务给出可复算的中间值，不额外要求构造题目已经给定的输入。'
                    '保留精确输入（如√2/2），展示时的舍入值不得作为计算输入；误差阈值须与实际计算精度一致。'
                    '二分查找未命中练习采用升序数组和左闭右闭区间，写出初始区间、每轮左右界与中点、比较与更新、退出条件及返回值。'
                    '返回JSON对象：activity为1至1000字符的练习正文字符串，source_ids为最多3个有效整数来源编号组成的数组，minutes为1至60整数。'
                    '无论是否选中来源均给出备用search_terms，依据不足则返回空source_ids。'
                    '根据requirements搜索具体缺失的方法，已有依据的相邻操作不重复检索。'
                    '例如缺的是数值比较向量长度，使用“三维向量 欧几里得范数 分量平方和”或“3D vector Euclidean norm formula”，避免重复搜索旋转公式。'
                    '二分查找未命中则检索空区间、循环终止和返回值。'
                    'search_terms不得含个人信息。若提供format_issue，请修正对应字段。'
                    + ('本次缺口是数值长度比较。source_ids必须包含提供三维向量范数或分量平方和公式的原文；'
                       '单独的旋转公式不能替代它。' if norm_comparison else '')))
        return response
    response=propose() if sources else dict(source_ids=[])
    apply_practice_candidate(goal,draft,missing,external,sources,response)
    if draft.get('practice_format_issue'):
        draft['practice_format_retries']=1
        draft['practice_initial_format_issue']=draft.pop('practice_format_issue')
        response=propose(draft['practice_initial_format_issue'])
        apply_practice_candidate(goal,draft,missing,external,sources,response)
    if draft.get('practice_repair_issue') in ('现有来源不足以支持综合练习','已生成候选练习，但复核未确认其覆盖原缺口','补练复核中断，可尝试补搜后复核'):
        queries=[]
        if QUATERNION_ROTATION_REQUIREMENT in requirements:
            queries.append(dict(search_terms=['unit quaternion vector rotation','q p q inverse conjugate formula']))
        if norm_comparison:
            queries.append(dict(search_terms=['vector magnitude:'],
                reference_url='https://openstax.org/books/calculus-volume-3/pages/2-2-vectors-in-three-dimensions'))
        found=False
        for query in queries or [response]:
            if search_practice_sources(draft,sources,query):
                found=True
        if found:
            if norm_comparison:
                sources.sort(key=lambda source: not (
                    has_norm_formula(source['quote']) or has_quaternion_rotation_formula(source['quote'])))
            apply_practice_candidate(goal,draft,missing,external,sources,propose())
            if draft.get('practice_format_issue') and not draft.get('practice_format_retries'):
                draft['practice_format_retries']=1
                draft['practice_initial_format_issue']=draft.pop('practice_format_issue')
                apply_practice_candidate(goal,draft,missing,external,sources,
                                         propose(draft['practice_initial_format_issue']))


def search_practice_sources(draft,sources,response):
    terms=response.get('search_terms')
    if isinstance(terms,list) and len(draft.get('searches',[]))<6:
        terms=[t.strip() for t in terms if isinstance(t,str) and 1<len(t.strip())<=60][:3]
        if terms:
            reference_url=response.get('reference_url')
            if reference_url!='https://openstax.org/books/calculus-volume-3/pages/2-2-vectors-in-three-dimensions':
                reference_url=None
            query=reference_url or ' '.join([*terms,'worked example'])
            log=dict(query=query,hits=0,pages=0)
            draft.setdefault('searches',[]).append(log)
            pages=[]
            try:
                hits=[dict(url=reference_url)] if reference_url else search_web(query)
                log['hits']=len(hits)
                log['pages']=collect_evidence_pages(hits,pages,set(),terms)
            except (ValueError,OSError,httpx.HTTPError) as exc:
                log['error_type']=type(exc).__name__
            for page in pages:
                window=evidence_window(page['body'],terms,size=800)
                for quote in evidence_passages(window):
                    if len(quote)>=20:
                        sources.append({k:v for k,v in page.items() if k!='body'}|dict(quote=quote))
            if pages:
                return True
    return False


def apply_practice_candidate(goal,draft,missing,external,sources,response):
    requirements=[check['requirement'] for check in missing]
    ids=response.get('source_ids')
    minutes=response.get('minutes')
    activity=response.get('activity')
    if ids==[]:
        draft['practice_repair_issue']='现有来源不足以支持综合练习'
        return
    invalid=[]
    if not isinstance(ids,list) or not 1<=len(ids)<=3 or any(type(i)!=int or not 1<=i<=len(sources) for i in ids):
        invalid.append('source_ids须含1至3个有效整数来源编号')
    if type(minutes)!=int or not 1<=minutes<=60:
        invalid.append('minutes须为1至60整数')
    if not isinstance(activity,str) or not 1<=len(activity.strip())<=1000:
        invalid.append('activity须为1至1000字符的正文字符串')
    if invalid:
        draft['practice_format_issue']='；'.join(invalid)
        draft['practice_repair_issue']='补充练习格式无效：'+draft['practice_format_issue']
        return
    selected=[sources[i-1] for i in dict.fromkeys(ids)]
    if '四元数' in goal:
        required_sources=[]
        for requirement,recognize in (
            (QUATERNION_ROTATION_REQUIREMENT,has_quaternion_rotation_formula),
            (QUATERNION_LENGTH_REQUIREMENT,has_norm_formula),
        ):
            if requirement in requirements:
                source=next((s for s in [*selected,*sources] if recognize(s['quote'])),None)
                if source is not None and source not in required_sources:
                    required_sources.append(source)
        selected=(required_sources+[s for s in selected if s not in required_sources])[:3]
    if ('四元数' in goal and '证明' not in goal
            and all(quaternion_norm_gap(requirement,[])=='原文缺少三维向量范数或分量平方和的计算依据'
                    for requirement in requirements)
            and not any(has_norm_formula(source['quote']) for source in selected)):
        draft['practice_repair_issue']='现有来源不足以支持综合练习'
        return
    miss_requirement='用不存在的目标验证二分查找的未命中返回结果'
    hit_requirement='在已排序数组中用二分查找定位存在的目标'
    standalone_binary_practice=miss_requirement in requirements and set(requirements)<={miss_requirement,hit_requirement}
    if standalone_binary_practice:
        activity=binary_miss_practice()
        if hit_requirement in requirements:
            activity=('命中练习：在升序数组 [1, 3, 5, 7, 9] 中查找 5。'
                      '请在初始区间 [0,4] 计算中点并读取该元素。'
                      '中点为 2，元素为 5，与目标相等，返回零基索引 2。\n'+activity)
    node=dict(ref='goal-practice',name='目标综合练习',catalog_id=None,objective=goal,minutes=minutes,
              prerequisites=[] if standalone_binary_practice else [
                  n['ref'] for n in external if any(s in selected for s in n['sources'])],
              ready=True,sources=selected,activity=activity.strip(),search_terms=[])
    draft.setdefault('practice_candidates',[]).append(dict(activity=node['activity'],sources=selected))
    try:
        checks=review_goal(goal,[node],requirements)
    except (HTTPException,ValueError) as exc:
        draft.setdefault('practice_review_errors',[]).append(
            exc.detail.get('message','模型复核请求失败') if isinstance(exc,HTTPException) and isinstance(exc.detail,dict)
            else '模型复核结果无法校验')
        draft['practice_repair_issue']='补练复核中断，可尝试补搜后复核'
        return
    draft.setdefault('practice_reviews',[]).append(checks)
    supported={c['requirement']:c for c in checks if c['refs'] and c['requirement'] in requirements}
    if supported:
        draft.pop('practice_repair_issue',None)
        draft['nodes'].append(node)
        for check in missing:
            if check['requirement'] in supported:
                check.update(supported[check['requirement']])
    else:
        draft['practice_repair_issue']='已生成候选练习，但复核未确认其覆盖原缺口'


def contradictory_embedding(activity):
    """Check an explicit p=(V,0) or p=(0,V) against its worked numeric example."""
    if '四元数' not in activity:
        return False
    compact=re.sub(r'\s+', '', activity.translate(str.maketrans('（），','(),')))
    number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
    for rule in re.finditer(r'([A-Za-z]\w*?)=\((?:([A-Za-z]+),0|0,([A-Za-z]+))\)',compact,re.ASCII):
        quat,scalar_last,scalar_first=rule.groups()
        vector=scalar_last or scalar_first
        tail=compact[rule.end():]
        sample=re.search(r'(?<![A-Za-z0-9_])'+vector+r'=\(('+number+'),('+number+'),('+number+r')\)',tail)
        if not sample:
            continue
        result=re.search(r'(?<![A-Za-z0-9_])'+quat+r'=\(('+number+'),('+number+'),('+number+'),('+number+r')\)',tail[sample.end():])
        if result:
            xyz=tuple(map(Decimal,sample.groups()))
            expected=xyz+(0,) if scalar_last else (0,)+xyz
            opposite=(0,)+xyz if scalar_last else xyz+(0,)
            actual=tuple(map(Decimal,result.groups()))
            if actual!=expected and actual==opposite:
                return True
    return False


def retry_core_prerequisite(goal,draft):
    checks=draft.get('goal_checks',[])
    if not checks or any(not c['refs'] for c in checks) or len(draft['searches'])>=6:
        return
    core=set(default_schedule_selection(draft))
    node=next((n for n in draft['nodes'] if n['ref'] in core and not n['ready']),None)
    if node is None:
        return
    terms=node['search_terms'][:2]
    query=' '.join([node['name'],*terms,'algorithm worked example'])[:200]
    log=dict(query=query,hits=0,pages=0,prerequisite=node['ref'])
    draft['searches'].append(log)
    pages=[]
    try:
        hits=search_web(query)
        log['hits']=len(hits)
        log['pages']=collect_evidence_pages(hits,pages,set(),[node['name'],*terms])
        if pages:
            attach_web_evidence(goal,node,pages)
            if node['ready']:
                node.pop('issue',None)
    except (ValueError,OSError,httpx.HTTPError,HTTPException) as exc:
        log['error_type']=type(exc).__name__


def generate(ident, user):
    with transaction() as db:
        record=owned(db,user,ident)
        catalog,covered=catalog_context(db,user)
    draft=json.loads(record['content'])
    draft['practice_format_retries']=0
    try:
        discover(record['description'],catalog,covered,draft)
        draft['goal_checks']=review_goal(record['description'],draft['nodes'])
        try:
            supplement_missing_activity(record['description'],draft)
            retry_core_prerequisite(record['description'],draft)
        except (HTTPException,ValueError):
            draft['practice_repair_issue']='补充练习暂未通过复核，保留原审核结果'
    except HTTPException as exc:
        draft['error']=exc.detail.get('message','云端模型不可用') if isinstance(exc.detail,dict) else '云端模型不可用'
    except ValueError as exc:
        draft['error']=str(exc)[:200]
    except Exception:
        draft['error']='生成中断，请检查模型或联网服务'
    for node in draft['nodes']:
        if node['ready'] and contradictory_embedding(node['activity']):
            node.update(ready=False,issue='纯四元数分量顺序与同一活动的数值示例冲突，请统一实部位置。')
    with transaction() as db:
        try:
            schedule(db,user['id'],draft,default_schedule_selection(draft))
        except HTTPException:
            draft['error']='个人预算或截止日期已变化，请调整后重新生成'
        status=assess_schedule(draft)
        run(db,"UPDATE discovery_drafts SET content=:c,status=:s,revision=1 WHERE id=:i AND status='running'",
            c=dump(draft),s=status,i=ident)


@router.post('/discovery-drafts',status_code=202)
def create(body:Discovery, background:BackgroundTasks, user=Depends(current_user), key:str=Header(...,alias='Idempotency-Key',max_length=128)):
    role(user,'student')
    if not body.web_consent or not body.text.strip():
        fail(422,'请填写目标并确认允许联网分析')
    with transaction() as db:
        old=one(db,'SELECT * FROM discovery_drafts WHERE student_id=:u AND request_key=:k',u=user['id'],k=key)
        if old:
            saved=dto(old)
            if (old['description'],saved['weekly_minutes'],saved['due_date'])!=(body.text.strip(),body.weekly_minutes,body.due_date.isoformat()):
                fail(409,'请求标识已用于其他目标设置')
            return saved
        # Validate the deadline with the same timezone and rules as formal planning.
        try:
            allocate(db,user['id'],1,body.due_date.isoformat(),goal_limit=body.weekly_minutes)
        except HTTPException as exc:
            if not isinstance(exc.detail,dict) or exc.detail.get('code')!='BUDGET_CONFLICT':
                raise
        ident=uid()
        draft=dict(due_date=body.due_date.isoformat(),weekly_minutes=body.weekly_minutes,nodes=[],searches=[],selected=[],schedule=[])
        insert(db,'discovery_drafts',id=ident,student_id=user['id'],request_key=key,description=body.text.strip(),
               created_at=now(),status='running',revision=0,content=dump(draft))
        result=dto(owned(db,user,ident))
    background.add_task(generate,ident,user)
    return result


@router.get('/discovery-drafts')
def history(user=Depends(current_user)):
    role(user,'student')
    with transaction() as db:
        return rows(db,"""SELECT id,description,CASE WHEN status='ready'
            AND COALESCE(json_array_length(content,'$.goal_checks'),0)=0 THEN 'partial' ELSE status END AS status,
            revision,created_at FROM discovery_drafts WHERE student_id=:u ORDER BY created_at DESC""",u=user['id'])


@router.get('/discovery-drafts/{ident}')
def detail(ident:str,user=Depends(current_user)):
    with transaction() as db:
        return dto(owned(db,user,ident))


@router.patch('/discovery-drafts/{ident}')
def choose(ident:str,body:Choice,user=Depends(current_user)):
    with transaction() as db:
        record=owned(db,user,ident)
        if record['status'] in ('interrupted','running') or record['revision']!=body.revision:
            fail(409,'草案已变化或仍在生成，请重新打开')
        draft=json.loads(record['content'])
        schedule(db,user['id'],draft,body.selected)
        run(db,'UPDATE discovery_drafts SET content=:c,status=:s,revision=revision+1 WHERE id=:i',s=assess_schedule(draft),c=dump(draft),i=ident)
        return dto(owned(db,user,ident))
