import json

import pytest
from pydantic import ValidationError

from app.api.v1.agents import AgentCreate, _decode_json


def test_decode_json_handles_asyncpg_jsonb_strings_and_native_values():
    payload = {"provider": "openai", "model": "gpt"}
    assert _decode_json(json.dumps(payload)) == payload
    assert _decode_json(payload) is payload


def test_agent_slug_rejects_trailing_hyphen():
    payload = {
        "slug": "inventory-agent-",
        "name": "Inventory Agent",
        "manifest": {"purpose": "Read inventory"},
        "model_settings": {"provider": "openai", "model": "gpt"},
    }
    with pytest.raises(ValidationError):
        AgentCreate.model_validate(payload)


def test_agent_slug_accepts_valid_slug():
    payload = {
        "slug": "inventory-agent",
        "name": "Inventory Agent",
        "manifest": {"purpose": "Read inventory"},
        "model_settings": {"provider": "openai", "model": "gpt"},
    }
    assert AgentCreate.model_validate(payload).slug == "inventory-agent"
