"""Narrow deterministic checks supplement, rather than replace, semantic review."""
import re

ACTIVITY_RULES = ('逐项保留引用示例中参数、输出、边界条件的含义。'
    '数值上限n与项数n不能互换。优先复现原文示例；改编练习必须明确标注改编、'
    '说明与原文的差异，不得描述为原文要求。没有充分依据时缩减活动。')

def source_activity_issues(tasks, sources):
    issues = []
    by_version = {s['version_id']: s for s in sources}
    for task in tasks:
        instruction = task.get('instructions', '')
        cited = [by_version[c['version_id']].get('excerpt', '')
                 for c in task.get('citations', []) if c.get('version_id') in by_version]
        source = '\n'.join(cited)
        # Known regression: official fib(n) bounds values, not sequence length.
        fibonacci = re.search(r'fibonacci|斐波那契', instruction, re.I)
        count = re.search(r'前\s*[nN]\s*项|first\s+n\s+(?:fibonacci\s+)?(?:terms|numbers)', instruction, re.I)
        bound = re.search(r'less than\s+n|below\s+n|小于\s*[nN]|up to\s+n', source, re.I)
        source_count = re.search(r'前\s*[nN]\s*项|first\s+n\s+(?:fibonacci\s+)?(?:terms|numbers)', source, re.I)
        if fibonacci and count and bound and not source_count:
            issues.append(f"{task.get('skill_id','')}: 引用示例以n为数值上限，任务改成前n项；请使用原文的上限语义完成本次活动。")
    return issues
