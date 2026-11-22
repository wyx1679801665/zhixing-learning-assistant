"""Short, skill-labelled lessons must expose their examples to path reviewers."""
from app.services.planning import source_location


def test_short_declared_lesson_citation_includes_exercise_code():
    body='# 递归与结束条件\n知识点 `recursion`\n'+'课程说明。'*80+'\ndef sum_to(n):\n    if n == 0:\n        return 0\n    return n + sum_to(n-1)\n'
    assert 300<len(body)<2000
    start,end=source_location(body,'catalog:p0-python-recursion',['recursion'],['recursion'],{})
    assert (start,end)==(0,len(body))
    assert 'return n + sum_to(n-1)' in body[start:end]


def test_long_or_unlabelled_document_remains_bounded():
    body='函数 functions 内容。'+'示例。'*1000
    start,end=source_location(body,'functions',['functions'],['functions'],{})
    assert end-start==300
    short='函数 functions 内容。'+'示例。'*70
    start,end=source_location(short,'functions',['functions'],[],{})
    assert end-start==min(300,len(short))
