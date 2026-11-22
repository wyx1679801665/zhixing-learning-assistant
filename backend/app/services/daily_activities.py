"""Attach work and observable outputs to allocated dates without changing capacity."""
import re

def attach_daily_activities(item):
    dates=item.get('dates') or []
    if not dates:return
    text=(item.get('instructions') or '').strip()
    # Split only at explicit activity boundaries, never inside code or formulas.
    parts=[s.strip() for s in re.split(r'(?:^|(?<=[。；;]))\s*[1-9][.、]\s*|[；;。](?=\s*(?:重点理解|完成(?:手算|讲义)|再用未见|最后[，,]\s*自检|编写|练习|自检|验证|复盘))',text) if s.strip()]
    name=item.get('name') or item['skill_id']
    total=sum(d['minutes'] for d in dates)
    if not total:return
    elapsed=0
    for slot_index,day in enumerate(dates):
        start=elapsed;elapsed+=day['minutes']
        count=len(parts)
        # Assign each ordered step by its start position in the time budget.
        indices=[i for i in range(count) if start*count<=i*total<elapsed*count]
        if count==len(dates):indices=[slot_index]
        if indices:
            action=' '.join(parts[i] for i in indices)
        elif parts:
            action='继续上一学习时段的活动：'+parts[min(count-1,start*count//total)]
        else:action='结合本项教材片段，整理“'+name+'”的一个例子。'
        day['activity']=action
        if elapsed==total:day['deliverable']='一份“'+name+'”自检记录：给出结果、解释依据，并标记仍需复习的问题。'
        elif re.search('手算|编写|运行|验证',action):day['deliverable']='一条可核对的练习记录：写下输入、预期结果和实际结果。'
        else:day['deliverable']='一条教材笔记：概括本段要点，注明例子及一个待验证的问题。'

def remaining_activities(previous, fraction):
    """Estimate a resume point; a cumulative check-in does not prove step completion."""
    from decimal import Decimal
    slots=previous.get('dates') or []
    if not slots or not all(d.get('activity') and d.get('minutes',0)>0 for d in slots):return []
    offset=Decimal(str(fraction))*sum(d['minutes'] for d in slots)
    # Float ratios such as 20/60 can land just below an exact minute boundary.
    nearest=offset.to_integral_value()
    if abs(offset-nearest)<Decimal('1e-9'):
        offset=nearest
    pending=[]
    for d in slots:
        span=d['minutes']
        if offset>=span:
            offset-=span
            continue
        pending.append(('接着完成：' if offset>0 else '')+d['activity'])
        offset=0
    return pending
