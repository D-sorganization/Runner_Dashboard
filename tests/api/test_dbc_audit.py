"""Meta-test: all POST routes have typed pydantic body models (issue #716)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))


def test_models_requests_module_exists():
    """backend/models/requests.py must exist with HelpChatRequest."""
    from models.requests import HelpChatRequest, LauncherGenerateRequest

    assert HelpChatRequest is not None
    assert LauncherGenerateRequest is not None


def test_help_chat_request_has_validation():
    """HelpChatRequest validates min_length."""
    from models.requests import HelpChatRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        HelpChatRequest(question="")


def test_launcher_request_validates_repo_format():
    """LauncherGenerateRequest validates repo format."""
    from models.requests import LauncherGenerateRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        LauncherGenerateRequest(repo_full="invalid")


def test_models_are_pydantic_base_models():
    """Both models should be Pydantic BaseModel subclasses."""
    from models.requests import HelpChatRequest, LauncherGenerateRequest
    from pydantic import BaseModel

    assert issubclass(HelpChatRequest, BaseModel)
    assert issubclass(LauncherGenerateRequest, BaseModel)


def test_help_chat_endpoint_validation(mock_auth):
    """Test help chat body validation and error handling format."""
    import server
    from fastapi.testclient import TestClient

    client = TestClient(server.app, raise_server_exceptions=False)
    headers = {"X-Requested-With": "XMLHttpRequest"}

    # 1. Valid payload - should succeed
    response = client.post(
        "/api/help/chat",
        json={"question": "fleet", "current_tab": "Fleet"},
        headers=headers,
    )
    assert response.status_code == 200
    assert "answer" in response.json()
    assert response.json()["source"] == "faq"

    # 2. Validation failure - empty question
    response = client.post(
        "/api/help/chat",
        json={"question": "", "current_tab": "Fleet"},
        headers=headers,
    )
    assert response.status_code == 422
    err_json = response.json()
    assert err_json["error"] == "validation_error"
    assert "body.question" in err_json["detail"]

    # 3. Validation failure - extra parameter forbidden
    response = client.post(
        "/api/help/chat",
        json={"question": "fleet", "extra_param": "forbidden"},
        headers=headers,
    )
    assert response.status_code == 422
    err_json = response.json()
    assert err_json["error"] == "validation_error"
    assert "extra_param" in err_json["detail"]
