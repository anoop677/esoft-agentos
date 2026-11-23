from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    approval_required: bool
    reason: str


def evaluate_tool_proposal(
    *,
    agent_status: str,
    autonomy_level: str,
    tool_permissions: list[dict],
    tool_id: str,
    action: str,
    catalog_enabled: bool,
    agents_enabled: bool,
) -> PolicyDecision:
    """Deterministic authorization. Model output is never an authorization source."""
    if not agents_enabled:
        return PolicyDecision(False, False, "Tenant agent kill switch is active")
    if agent_status != "active":
        return PolicyDecision(False, False, "Agent is not active")
    if not catalog_enabled:
        return PolicyDecision(False, False, "Tool is unavailable or disabled")
    if autonomy_level not in {"A0", "A1", "A2", "A3"}:
        return PolicyDecision(False, False, "Unknown autonomy level")

    permission = next(
        (
            item for item in tool_permissions
            if item.get("tool_id") == tool_id and action in item.get("actions", [])
        ),
        None,
    )
    if permission is None:
        return PolicyDecision(False, False, "Tool action is not in the agent version allowlist")

    # This milestone never authorizes direct execution. A2/A3 still require
    # an approval record until a separately reviewed executor is introduced.
    approval_required = bool(permission.get("approval_required", True)) or autonomy_level in {
        "A2", "A3"
    }
    return PolicyDecision(True, approval_required, "Allowed by deterministic policy")
