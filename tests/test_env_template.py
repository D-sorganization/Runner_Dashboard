"""Setup templates must not resurrect retired Maxwell proxy configuration."""

from pathlib import Path


def test_env_example_has_no_maxwell_proxy_vars() -> None:
    """Maxwell-Daemon proxy env vars were removed with F15 (#1338, 2026-10-23)."""
    template = Path(__file__).resolve().parents[1] / ".env.example"
    text = template.read_text(encoding="utf-8")
    for key in ("MAXWELL_URL", "MAXWELL_PORT", "MAXWELL_API_TOKEN"):
        assert f"{key}=" not in text
