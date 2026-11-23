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
    risk_tier: str,
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

    if autonomy_level in {"A0", "A1"} and action.lower() not in {
        "read", "list", "get", "search"
    }:
        return PolicyDecision(False, False, "This autonomy level permits read-only actions")

    # Riskier catalog entries always require human approval, regardless of
    # an agent-authored permission flag. A2/A3 also remain approval-gated.
    approval_required = (
        bool(permission.get("approval_required", True))
        or risk_tier in {"medium", "high", "critical"}
        or autonomy_level in {"A2", "A3"}
    )
    return PolicyDecision(True, approval_required, "Allowed by deterministic policy")


def sanitize_arguments(value: dict, *, depth: int = 0) -> dict:
    """Redact credential-like fields before arguments enter the approval inbox."""
    if depth > 10:
        return {"_truncated": "maximum nesting depth exceeded"}

    sensitive_fragments = (
        "password", "secret", "token", "authorization", "credential",
        "apikey", "privatekey", "accesskey",
    )
    sanitized = {}
    for key, item in value.items():
        normalized = "".join(character for character in str(key).lower() if character.isalnum())
        if any(fragment in normalized for fragment in sensitive_fragments):
            sanitized[key] = "[REDACTED]"
        elif isinstance(item, dict):
            sanitized[key] = sanitize_arguments(item, depth=depth + 1)
        elif isinstance(item, list):
            sanitized[key] = [
                sanitize_arguments(entry, depth=depth + 1) if isinstance(entry, dict) else entry
                for entry in item
            ]
        else:
            sanitized[key] = item
    return sanitized
