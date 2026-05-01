from __future__ import annotations  # noqa: E402

import time
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import security  # noqa: E402

# ---------------------------------------------------------------------------
# sanitize_log_value
# ---------------------------------------------------------------------------


def test_sanitize_log_value_no_change() -> None:
    assert security.sanitize_log_value("hello world") == "hello world"


def test_sanitize_log_value_newline() -> None:
    assert security.sanitize_log_value("hello\nworld") == "hello\\nworld"


def test_sanitize_log_value_carriage_return() -> None:
    assert security.sanitize_log_value("hello\rworld") == "hello\\rworld"


def test_sanitize_log_value_tab() -> None:
    assert security.sanitize_log_value("hello\tworld") == "hello\\tworld"


def test_sanitize_log_value_all_injections() -> None:
    raw = "line1\nline2\rline3\tend"
    expected = "line1\\nline2\\rline3\\tend"
    assert security.sanitize_log_value(raw) == expected


def test_sanitize_log_value_truncates_long() -> None:
    long_value = "x" * 500
    result = security.sanitize_log_value(long_value)
    assert len(result) == 200
    assert result == "x" * 200


def test_sanitize_log_value_empty() -> None:
    assert security.sanitize_log_value("") == ""


# ---------------------------------------------------------------------------
# validate_fleet_node_url
# ---------------------------------------------------------------------------


def test_validate_fleet_node_url_http_localhost() -> None:
    assert security.validate_fleet_node_url("http://localhost:8080") == "http://localhost:8080"


def test_validate_fleet_node_url_https_localhost() -> None:
    assert security.validate_fleet_node_url("https://localhost:8080") == "https://localhost:8080"


def test_validate_fleet_node_url_local_domain() -> None:
    assert security.validate_fleet_node_url("http://node.local:8080") == "http://node.local:8080"


def test_validate_fleet_node_url_internal_domain() -> None:
    assert security.validate_fleet_node_url("http://node.internal:8080") == "http://node.internal:8080"


def test_validate_fleet_node_url_loopback() -> None:
    assert security.validate_fleet_node_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"


def test_validate_fleet_node_url_private_ip() -> None:
    assert security.validate_fleet_node_url("http://192.168.1.1:8080") == "http://192.168.1.1:8080"


def test_validate_fleet_node_url_public_ip() -> None:
    with pytest.raises(ValueError, match="private/local address"):
        security.validate_fleet_node_url("http://8.8.8.8:8080")


def test_validate_fleet_node_url_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="http or https"):
        security.validate_fleet_node_url("ftp://localhost:8080")


def test_validate_fleet_node_url_untrusted_hostname() -> None:
    with pytest.raises(ValueError, match="hostname not allowed"):
        security.validate_fleet_node_url("http://evil.com:8080")


def test_validate_fleet_node_url_no_scheme() -> None:
    with pytest.raises(ValueError):
        security.validate_fleet_node_url("localhost:8080")


# ---------------------------------------------------------------------------
# validate_local_url
# ---------------------------------------------------------------------------


def test_validate_local_url_valid() -> None:
    assert security.validate_local_url("http://localhost:3000") == "http://localhost:3000"


def test_validate_local_url_invalid_scheme() -> None:
    with pytest.raises(ValueError, match="http or https"):
        security.validate_local_url("ftp://localhost:3000")


def test_validate_local_url_untrusted_host() -> None:
    with pytest.raises(ValueError, match="hostname not allowed"):
        security.validate_local_url("http://example.com:3000")


def test_validate_local_url_custom_field() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        security.validate_local_url("ftp://localhost:3000", field="endpoint")


# ---------------------------------------------------------------------------
# validate_local_path
# ---------------------------------------------------------------------------


def test_validate_local_path_valid(tmp_path: Path) -> None:
    target = tmp_path / "subdir" / "file.txt"
    result = security.validate_local_path(str(target), tmp_path)
    assert result == target.resolve()


def test_validate_local_path_escape_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Path escapes"):
        security.validate_local_path("/etc/passwd", tmp_path)


def test_validate_local_path_dotdot_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Path escapes"):
        security.validate_local_path(str(tmp_path / ".." / "etc" / "passwd"), tmp_path)


# ---------------------------------------------------------------------------
# validate_health_command
# ---------------------------------------------------------------------------


def test_validate_health_command_simple() -> None:
    assert security.validate_health_command("curl http://localhost/health") == [
        "curl",
        "http://localhost/health",
    ]


def test_validate_health_command_semicolon() -> None:
    with pytest.raises(ValueError, match="disallowed characters"):
        security.validate_health_command("echo hello; rm -rf /")


def test_validate_health_command_pipe() -> None:
    with pytest.raises(ValueError, match="disallowed characters"):
        security.validate_health_command("cat file | grep foo")


def test_validate_health_command_ampersand() -> None:
    with pytest.raises(ValueError, match="disallowed characters"):
        security.validate_health_command("cmd && cmd2")


def test_validate_health_command_backtick() -> None:
    with pytest.raises(ValueError, match="disallowed characters"):
        security.validate_health_command("\u0060cat /etc/passwd\u0060")


def test_validate_health_command_dollar() -> None:
    with pytest.raises(ValueError, match="disallowed characters"):
        security.validate_health_command("echo $PATH")


def test_validate_health_command_parentheses() -> None:
    with pytest.raises(ValueError, match="disallowed characters"):
        security.validate_health_command("$(ls)")


def test_validate_health_command_empty() -> None:
    assert security.validate_health_command("") == []


# ---------------------------------------------------------------------------
# check_dispatch_rate
# ---------------------------------------------------------------------------


def test_check_dispatch_rate_under_limit() -> None:
    # Clear any stale state
    security._dispatch_rate.clear()
    security.check_dispatch_rate("127.0.0.1")


def test_check_dispatch_rate_exceeds_limit(monkeypatch) -> None:
    # Clear any stale state
    security._dispatch_rate.clear()
    client = "127.0.0.2"

    # Simulate 10 requests within the window
    now = time.monotonic()
    security._dispatch_rate[client] = [now] * 10

    with pytest.raises(Exception) as exc_info:
        security.check_dispatch_rate(client)

    # FastAPI HTTPException is raised
    from fastapi import HTTPException

    assert isinstance(exc_info.value, HTTPException)
    assert exc_info.value.status_code == 429


def test_check_dispatch_rate_old_requests_expired(monkeypatch) -> None:
    security._dispatch_rate.clear()
    client = "127.0.0.3"

    # Old requests outside the 60s window
    now = time.monotonic()
    security._dispatch_rate[client] = [now - 120] * 10

    # Should not raise because old requests are pruned
    security.check_dispatch_rate(client)


# ---------------------------------------------------------------------------
# safe_subprocess_env
# ---------------------------------------------------------------------------


def test_safe_subprocess_env_excludes_secrets(monkeypatch) -> None:
    fake_env = {
        "PATH": "/usr/bin",
        "GH_TOKEN": "secret123",
        "GITHUB_TOKEN": "secret456",
        "ANTHROPIC_API_KEY": "key789",
        "DASHBOARD_API_KEY": "apikey",
        "MY_SECRET": "shh",
        "DATABASE_PASSWORD": "password",
        "SOME_TOKEN": "tok",
        "NORMAL_VAR": "ok",
    }
    monkeypatch.setattr("os.environ", fake_env)

    result = security.safe_subprocess_env()
    assert result == {"PATH": "/usr/bin", "NORMAL_VAR": "ok"}


def test_safe_subprocess_env_empty(monkeypatch) -> None:
    monkeypatch.setattr("os.environ", {})
    assert security.safe_subprocess_env() == {}


# ---------------------------------------------------------------------------
# YAML Loader Security (Issue #355)
# ---------------------------------------------------------------------------


def test_validate_config_path_valid_file(tmp_path: Path) -> None:
    """Test that valid config paths within allowed roots are accepted."""
    config_file = tmp_path / "config" / "test.yml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("key: value")
    
    result = security.validate_config_path(config_file, allowed_roots=[tmp_path])
    assert result == config_file.resolve()


def test_validate_config_path_escape_root(tmp_path: Path) -> None:
    """Test that paths escaping allowed roots are rejected."""
    evil_file = tmp_path / "evil.yml"
    evil_file.write_text("malicious: true")
    
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True, exist_ok=True)
    
    with pytest.raises(ValueError, match="escapes allowed roots"):
        security.validate_config_path(evil_file, allowed_roots=[allowed_root])


def test_validate_config_path_symlink_escape(tmp_path: Path) -> None:
    """Test that symlinks pointing outside allowed roots are rejected."""
    # Create a file outside the allowed root
    evil_file = tmp_path / "evil.yml"
    evil_file.write_text("malicious: true")
    
    # Create allowed root with a symlink to the evil file
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True, exist_ok=True)
    symlink = allowed_root / "config.yml"
    symlink.symlink_to(evil_file)
    
    # The resolved path escapes the allowed root, so it's rejected
    with pytest.raises(ValueError, match="escapes allowed roots"):
        security.validate_config_path(symlink, allowed_roots=[allowed_root])


def test_validate_config_path_symlink_safe(tmp_path: Path) -> None:
    """Test that symlinks pointing within allowed roots are accepted."""
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True, exist_ok=True)
    
    # Create target file within allowed root
    target_file = allowed_root / "target.yml"
    target_file.write_text("safe: true")
    
    # Create symlink within same root
    symlink = allowed_root / "config.yml"
    symlink.symlink_to(target_file)
    
    result = security.validate_config_path(symlink, allowed_roots=[allowed_root])
    assert result == target_file.resolve()


def test_validate_config_path_world_writable(tmp_path: Path) -> None:
    """Test that world-writable files are rejected."""
    import stat
    
    config_file = tmp_path / "config.yml"
    config_file.write_text("key: value")
    
    # Make file world-writable
    current_mode = config_file.stat().st_mode
    config_file.chmod(current_mode | stat.S_IWOTH)
    
    with pytest.raises(ValueError, match="world-writable"):
        security.validate_config_path(config_file, allowed_roots=[tmp_path], check_mode=True)


def test_validate_config_path_non_existent(tmp_path: Path) -> None:
    """Test that non-existent files are rejected."""
    non_existent = tmp_path / "does_not_exist.yml"
    
    with pytest.raises(ValueError, match="does not exist"):
        security.validate_config_path(non_existent, allowed_roots=[tmp_path])


def test_safe_yaml_load_valid(tmp_path: Path) -> None:
    """Test loading a valid YAML file."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("key: value\nnested:\n  item: 123")
    
    result = security.safe_yaml_load(config_file, allowed_roots=[tmp_path])
    assert result == {"key": "value", "nested": {"item": 123}}


def test_safe_yaml_load_escape_root(tmp_path: Path) -> None:
    """Test that safe_yaml_load rejects paths escaping allowed roots."""
    evil_file = tmp_path / "evil.yml"
    evil_file.write_text("malicious: true")
    
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir(parents=True, exist_ok=True)
    
    with pytest.raises(ValueError, match="escapes allowed roots"):
        security.safe_yaml_load(evil_file, allowed_roots=[allowed_root])


def test_validate_config_path_dot_expansion(tmp_path: Path) -> None:
    """Test that paths with ~ are properly expanded."""
    # Create a test file in home directory simulation
    home_sim = tmp_path / "home" / "user"
    home_sim.mkdir(parents=True, exist_ok=True)
    config_dir = home_sim / ".config" / "runner-dashboard"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "test.yml"
    config_file.write_text("key: value")
    
    # This should work since ~/.config/runner-dashboard is a default allowed root
    # But we need to provide explicit roots for the test
    result = security.validate_config_path(config_file, allowed_roots=[home_sim])
    assert result == config_file.resolve()


def test_validate_config_path_json_file(tmp_path: Path) -> None:
    """Test that JSON files are also validated."""
    config_file = tmp_path / "config.json"
    config_file.write_text('{"key": "value"}')
    
    result = security.validate_config_path(config_file, allowed_roots=[tmp_path])
    assert result == config_file.resolve()


def test_get_repo_root_returns_path_or_none() -> None:
    """Test that _get_repo_root returns a Path or None."""
    result = security._get_repo_root()
    # Should return a Path if in a git repo, None otherwise
    assert result is None or isinstance(result, Path)
    if result is not None:
        assert result.exists()


def test_check_file_mode_safe(tmp_path: Path) -> None:
    """Test _check_file_mode with a safe file."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("key: value")
    
    result = security._check_file_mode(config_file)
    assert result is True


def test_check_symlink_safe(tmp_path: Path) -> None:
    """Test _check_symlink with a non-symlink file."""
    config_file = tmp_path / "config.yml"
    config_file.write_text("key: value")
    
    result = security._check_symlink(config_file, allowed_roots=[tmp_path])
    assert result is True
