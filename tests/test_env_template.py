"""Setup templates must target the documented co-located daemon listener."""

from pathlib import Path


def test_env_example_targets_default_maxwell_listener() -> None:
    """Copying the template must not restore the dashboard port collision (#959)."""
    template = Path(__file__).resolve().parents[1] / ".env.example"
    assignments = dict(
        line.split("=", 1)
        for line in template.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert assignments["MAXWELL_PORT"] == "8080"
