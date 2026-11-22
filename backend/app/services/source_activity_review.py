"""Narrow deterministic checks supplement, rather than replace, semantic review."""
import re

ACTIVITY_RULES = ('逐项保留引用示例中参数、输出、边界条件的含义。'
    '先检查来源coverage_scope；partial_excerpt仅为部分内容，complete_stored_document仅为保存的文档版本。'
    '禁止把片段未展示推断为原文没有；不要使用“原文未提供”“文档不存在”等无依据的全文否定。'
    '依据不足时表述为“当前提供的材料未展示”，缩减任务或要求补充来源。'
    '数值上限n与项数n不能互换。优先复现原文示例；改编练习必须明确标注改编、'
    '说明与原文的差异，不得描述为原文要求。函数名沿用来源；新函数名必须声明为自定义。'
    '涉及print与return的改编，必须解释return结束当前调用，禁止仅说把print替换成return。'
    '若要求把循环输出改为返回完整列表，必须明确初始化结果列表、循环内追加、循环结束后return列表，'
    '并提供与引用一致的具体输入输出或边界验收标准。审查时检查提前返回、返回类型及函数名是否一致。'
    '改动return与print时必须点名被修改的分支，输入须实际经过该分支。'
    'sum_to删除基本情况而保留负数检查时，应明确预测ValueError，不写“不终止或报错”这类二选一结论。'
    '引用当前课程的first/second/third示例时，second改为return third()+"B"后，first结果为"ACB"；'
    'sum_to的if n==0和return 0分两行，移除基本情况须指明整个分支而不是一行。'
    'C++反例若在函数体中写入const引用，应先单独编译运行正确示例，再将反例置于独立源文件仅编译并记录诊断。错误发生在函数定义处。'
    '没有充分依据时缩减活动。')

def source_activity_issues(tasks, sources):
    issues = []
    by_version = {s['version_id']: s for s in sources}
    for task in tasks:
        instruction = task.get('instructions', '')
        cited = [by_version[c['version_id']].get('excerpt', '')
                 for c in task.get('citations', []) if c.get('version_id') in by_version]
        source = '\n'.join(cited)
        for url in re.findall(r'https?://[^\s<>\]）)"\']+', instruction, re.I):
            if url.rstrip('.,，。') not in source:
                issues.append(f"{task.get('skill_id','')}: 活动要求使用未在引用正文中给出的外部链接；请删除链接或补充获授权的来源。")
                break
        if re.search(r'(?:观看|播放|打开|学习)\s*[^。；\n]{0,24}视频', instruction) and not ('视频' in source and re.search(r'https?://',source,re.I)):
            issues.append(f"{task.get('skill_id','')}: 活动要求观看视频，但引用正文没有可核对的视频地址；请改用已提供的正文活动或补充授权视频。")
        if (re.search(r'(?:删除|注释掉?)[^。；\n]{0,35}if\s+n\s*==\s*0',instruction)
                and re.search(r'if\s+n\s*<\s*0',source)
                and not (re.search(r'整个分支|连同[^。；\n]{0,20}return\s+0',instruction)
                         and 'ValueError' in instruction)):
            issues.append(f"{task.get('skill_id','')}: 删除基本情况的表述不完整；若只删 if 行会导致缩进错误，删整个分支后保留负数检查则会抛出 ValueError。请明确操作和预期。")
        if (re.search(r'编写三个函数[：:]\s*void\s+step\s*\(\s*int\s+copy\s*,\s*int\s*&\s*original', instruction)
                and len(set(re.findall(r'\bvoid\s+(\w+)\s*\(', instruction))) == 1):
            issues.append(f"{task.get('skill_id','')}: 这里只给出一个双参数函数。请写明先运行 step(x,x)，记录 x 从1变为3；保存结果后复制源文件，将副本中的 original 改为 const int& 并仅编译副本，记录写入只读引用的诊断。")
        # Catch the mixed runnable/ill-formed exercise seen in the live C++ trial.
        invalid_write = re.search(
            r'const\s+int\s*&\s*(\w+)\s*\)\s*\{\s*\1\s*\+=', instruction)
        if not invalid_write:
            invalid_write = re.search(
                r'by_const_ref\s*\(\s*const\s+int\s*&\s*(\w+)\s*\)'
                r'[^。；\n]{0,65}(?:分别|均|都)[^。；\n]{0,35}\b\1\s*\+=', instruction)
        mixed_run = re.search(r'(?:运行|打印|观察|调用|传入)', instruction)
        separate_compile = re.search(r'(?:单独|独立|另建|另存|另一个)[^。；\n]{0,24}(?:编译|文件|程序)', instruction)
        if invalid_write and (mixed_run and not separate_compile or re.search(r'(?:依次|分别)调用[^。；\n]*(?:三个函数|这三个|by_const_ref)', instruction)):
            issues.append(f"{task.get('skill_id','')}: 写入const引用会使函数定义编译失败。先用独立程序运行两个正确函数，再单独编译错误定义并记录诊断。")
        # Known regression: official fib(n) bounds values, not sequence length.
        fibonacci = re.search(r'fibonacci|斐波那契', instruction, re.I)
        count = re.search(r'前\s*[nN]\s*项|first\s+n\s+(?:fibonacci\s+)?(?:terms|numbers)', instruction, re.I)
        bound = re.search(r'less than\s+n|below\s+n|小于\s*[nN]|up to\s+n', source, re.I)
        source_count = re.search(r'前\s*[nN]\s*项|first\s+n\s+(?:fibonacci\s+)?(?:terms|numbers)', source, re.I)
        if fibonacci and count and bound and not source_count:
            issues.append(f"{task.get('skill_id','')}: 引用示例以n为数值上限，任务改成前n项；请使用原文的上限语义完成本次活动。")
        if (re.search(r'double_if_positive\s*\(\s*3\s*\)',instruction)
                and re.search(r'n\s*>\s*0[^。；\n]{0,35}(?:2\s*\*\s*n|2\s*\*\s*n)',instruction)
                and re.search(r'double_if_positive\s*\(\s*3\s*\)[^。；\n]{0,50}(?:None|空值)',instruction)):
            issues.append(f"{task.get('skill_id','')}: 正数输入3仍走返回2*n的分支，结果为6；不能写成None。")
        if (re.search(r'double_if_positive\s*\(\s*3\s*\)[^。；\n]{0,40}None',instruction)
                and re.search(r'return[^。；\n]{0,12}(?:改为|替换为)\s*`?print',instruction)
                and not re.search(r'(?:正数|n\s*>\s*0|2\s*\*\s*n)[^。；\n]{0,22}return[^。；\n]{0,12}(?:改为|替换为)',instruction)):
            issues.append(f"{task.get('skill_id','')}: 把return改为print须明确是正数分支，否则输入3仍返回6；请写出具体改动。")
        if (re.search(r'sum_to\s*\(\s*3\s*\)',instruction)
                and re.search(r'(?:注释|删除)[^。；\n]{0,28}n\s*==\s*0',instruction)
                and re.search(r'(?:不终止|无限递归)[^。；\n]{0,15}(?:或|或者)[^。；\n]{0,15}ValueError',instruction)
                and re.search(r'n\s*<\s*0',source)):
            issues.append(f"{task.get('skill_id','')}: 保留负数检查并移除基本情况后，sum_to(3)会到达负数并抛ValueError；请给出唯一明确结论。")
        if (re.search(r'sum_to\s*\(\s*2\s*\)',instruction)
                and re.search(r'返回值[^。；\n]{0,20}3\s*[、,，]\s*2\s*[、,，]\s*0',instruction)):
            issues.append(f"{task.get('skill_id','')}: sum_to(2)、sum_to(1)、sum_to(0) 的返回值应为3、1、0。")
        if (re.search(r'third\s*\(\s*\)\s*\+\s*["“]B["”]',instruction)
                and re.search(r'first\s*\(\s*\)[^。；\n]{0,30}["“]CBA["”]',instruction)
                and (re.search(r'return\s*["“]A["”]\s*\+\s*second\s*\(\s*\)',source)
                     or task.get('skill_id','').endswith('stack'))):
            issues.append(f"{task.get('skill_id','')}: second改为C+B时，first仍拼接A，最终为ACB而非CBA。")
    return issues
