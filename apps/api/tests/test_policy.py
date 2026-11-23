import pytest

from app.infrastructure.policy import evaluate_tool_proposal


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"agents_enabled": False}, "Tenant agent kill switch is active"),
        ({"agent_status": "disabled"}, "Agent is not active"),
        ({"catalog_enabled": False}, "Tool is unavailable or disabled"),
        ({"tool_permissions": []}, "Tool action is not in the agent version allowlist"),
    ],
)
def test_policy_denies_unsafe_or_unconfigured_tool_proposals(overrides, reason):
    values = {
        "agent_status": "active",
        "autonomy_level": "A1",
        "tool_permissions": [{"tool_id": "inventory.lookup", "actions": ["read"], "approval_required": True}],
        "tool_id": "inventory.lookup",
        "action": "read",
        "catalog_enabled": True,
        "agents_enabled": True,
    }
    values.update(overrides)
    decision = evaluate_tool_proposal(**values)
    assert decision.allowed is False
    assert decision.reason == reason


def test_policy_requires_approval_for_configured_tool_action():
    decision = evaluate_tool_proposal(
        agent_status="active",
        autonomy_level="A1",
        tool_permissions=[{"tool_id": "inventory.lookup", "actions": ["read"], "approval_required": False}],
        tool_id="inventory.lookup",
        action="read",
        catalog_enabled=True,
        agents_enabled=True,
    )
    assert decision.allowed is True
    assert decision.approval_required is False


def test_autonomous_levels_still_require_approval_in_initial_runtime():
    decision = evaluate_tool_proposal(
        agent_status="active",
        autonomy_level="A3",
        tool_permissions=[{"tool_id": "inventory.lookup", "actions": ["read"], "approval_required": False}],
        tool_id="inventory.lookup",
        action="read",
        catalog_enabled=True,
        agents_enabled=True,
    )
    assert decision.allowed is True
    assert decision.approval_required is True
