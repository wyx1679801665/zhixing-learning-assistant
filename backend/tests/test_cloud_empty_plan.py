import pytest
from fastapi import HTTPException
from app.services import cloud_planning


def test_empty_plan_stops_before_paid_build_and_review(monkeypatch):
    monkeypatch.setattr(cloud_planning.planning, 'build', lambda state: state.update(execution=[]))
    def unexpected(*args, **kwargs):
        pytest.fail('An empty plan must not request paid task generation')
    monkeypatch.setattr(cloud_planning, 'agent_call', unexpected)
    with pytest.raises(HTTPException) as caught:
        cloud_planning.build({})
    assert caught.value.status_code == 409
    assert caught.value.detail['code'] == 'NO_PENDING_LEARNING_TASKS'
