from app.services.source_activity_review import source_activity_issues


def test_engineering_examples_reject_false_answers():
    source = [{'version_id': 'lesson', 'excerpt': 'def first():\n    return "A" + second()'}]
    tasks = [
        {'skill_id':'functions','instructions':'定义 n > 0 时返回 2 * n；result = double_if_positive(3) 后 result 为 None。','citations':[{'version_id':'lesson'}]},
        {'skill_id':'stack','instructions':'追踪 sum_to(2) 三层返回值（3、2、0）；修改 second 为 return third() + "B"，预测 first() 为 "CBA"。','citations':[{'version_id':'lesson'}]},
    ]
    issues = source_activity_issues(tasks, source)
    assert len(issues) == 3


def test_engineering_examples_require_unambiguous_branch_and_exception():
    source = [{'version_id':'lesson','excerpt':'if n < 0:\n    raise ValueError()\nif n == 0:\n    return 0'}]
    tasks = [
        {'skill_id':'functions','instructions':'若将return改为print，result = double_if_positive(3)后result为None。','citations':[{'version_id':'lesson'}]},
        {'skill_id':'recursion','instructions':'注释掉if n == 0后，sum_to(3)不终止或抛出ValueError。','citations':[{'version_id':'lesson'}]},
    ]
    assert len(source_activity_issues(tasks,source)) >= 2


def test_engineering_review_catches_full_document_examples_outside_excerpt():
    source = [{'version_id':'lesson','excerpt':'调用栈的三层返回顺序'}]
    tasks = [{'skill_id':'catalog:p0-python-stack',
              'instructions':'修改second函数为return third() + "B"，预测first()结果为"CBA"。',
              'citations':[{'version_id':'lesson'}]}]
    assert len(source_activity_issues(tasks,source)) == 1

def check(text, excerpt='Return Fibonacci numbers less than n.'):
    return source_activity_issues([{'skill_id':'functions','instructions':text,'citations':[{'version_id':'official'}]}],
                                 [{'version_id':'official','excerpt':excerpt}])

def test_value_bound_cannot_silently_become_count():
    assert check('编写返回前n项斐波那契数的函数。')
    assert check('Return the first n Fibonacci numbers.')
    assert check('Fibonacci exercise: return first n terms.')

def test_original_bound_is_allowed():
    assert not check('返回小于n的斐波那契数，核对返回列表。')

def test_count_allowed_when_cited_source_supports_it():
    assert not check('返回前n项斐波那契数。','Return the first n terms of Fibonacci.')

def test_unrelated_or_insufficient_source_is_left_to_semantic_review():
    assert not check('计算前n项求和。')
    assert not check('返回前n项斐波那契数。','A function uses return.')


def test_generated_external_material_requires_cited_address():
    assert check('先观看递归讲解视频，再完成练习。','递归函数要有基本情况。')
    assert check('阅读 https://example.org/extra 后作答。','递归函数要有基本情况。')
    assert not check('阅读 https://example.org/lesson 后作答。','课程地址 https://example.org/lesson')


def test_recursion_base_case_removal_must_explain_existing_negative_guard():
    source='if n < 0:\n    raise ValueError("n must be non-negative")\nif n == 0:\n    return 0\nreturn n + sum_to(n - 1)'
    assert check('删除 `if n == 0:` 后分析 sum_to(3) 的行为。',source)
    assert not check('删除 `if n == 0: return 0` 整个分支，保留负数检查；预测遇到 -1 时抛出 ValueError。',source)

def test_model_approval_cannot_override_source_conflict(monkeypatch):
    from contextlib import nullcontext
    from app.services import cloud_planning
    monkeypatch.setattr(cloud_planning,'transaction',lambda:nullcontext(None))
    monkeypatch.setattr(cloud_planning,'validate',lambda state,db:[{'passed':True}])
    monkeypatch.setattr(cloud_planning,'authorized_sources',lambda state:[
        {'version_id':'official','supported_skills':['functions'],'excerpt':'Return Fibonacci numbers less than n.'}])
    monkeypatch.setattr(cloud_planning,'agent_call',lambda *args:{'approved':True,'issues':[]})
    monkeypatch.setattr(cloud_planning.planning,'record_cloud_step',lambda *args:None)
    state={'execution':[{'skill_id':'functions','instructions':'返回前n项斐波那契数。',
                         'citations':[{'version_id':'official'}]}],'cloud_outputs':{},'learner_evidence':{}}
    result=cloud_planning.review(state)
    assert result['cloud_approved'] is False
    assert result['cloud_repairs']==1
    assert result['cloud_issues']


def test_const_reference_compile_error_kept_separate_from_runtime_experiment():
    from app.services.source_activity_review import source_activity_issues
    code = 'void by_const_ref(const int& n) { n += 2; }'
    def check(activity):
        return source_activity_issues([{'instructions': code + activity}], [])
    assert check('依次调用by_value(x)、by_ref(x)、by_const_ref(x)，每次打印x。')
    assert not check('先在独立程序运行前两个函数。单独编译上述错误定义并记录诊断。')
