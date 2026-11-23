import json

from pydantic import TypeAdapter, ValidationError

from app.api.v1.agents import AgentCreate, _decode_json


def test_decode_json_handles_asyncpg_jsonb_strings_and_native_values():
    payload = {"provider": "openai", "model": "gpt"}
    assert _decode_json(json.dumps(payload)) == payload
    assert _decode_json(payload) is payload


def test_agent_slug_accepts_single_character_suffix_without_trailing_hyphen():
    slug_type = AgentCreate.model_fields["slug"].annotation
    adapter = TypeAdapter(slug_type)
    assert adapter.validate_python("inventory-agent") == "inventory-agent"
    with ___error():
        adapter.validate_python("inventory-agent-")


class __error:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        assert exc_type is ValidationError
        return True
