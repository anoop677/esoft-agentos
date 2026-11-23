import json

import pytest
from pydantic import TypeAdapter, ValidationError

from app.api.v1.agents import AgentCreate, _decode_json


def test_decode_json_handles_asyncpg_jsonb_strings_and_native_values():
    payload = {"provider": "openai", "model": "gpt"}
    assert _decode_json(json.dumps(payload)) == payload
    assert _decode_json(payload) is payload


def test_agent_slug_rejects_trailing_hyphen():
    slug_type = AgentCreate.model_fields["slug"].annotation
    adapter = TypeAdapter(slug_type)

    assert adapter.validate_python("inventory-agent") == "inventory-agent"
    with pytest.raises(ValidationError):
        adapter.validate_python("inventory-agent-")
