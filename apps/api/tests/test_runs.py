import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.api.v1.runs import AgentRunCreate, _run_from_row


def test_run_input_rejects_payloads_larger_than_64_kib():
    with pytest.raises(ValidationError):
        AgentRunCreate(input={"prompt": "x" * 65537})


def test_run_response_decodes_asyncpg_jsonb_values():
    run_id = uuid4()
    agent_id = uuid4()
    version_id = uuid4()
    now = datetime.now(UTC)
    row = {
        "id": run_id,
        "agent_id": agent_id,
        "agent_version_id": version_id,
        "status": "queued",
        "input": json.dumps({"question": "stock status"}),
        "output": None,
        "error_code": None,
        "error_message": None,
        "attempt_count": 0,
        "input_tokens": None,
        "output_tokens": None,
        "model_name": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "finished_at": None,
    }

    result = _run_from_row(row)
    assert result.id == run_id
    assert result.input == {"question": "stock status"}
    assert result.output is None
