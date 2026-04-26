import sys  # noqa: E402
from pathlib import Path  # noqa: E402

backend_dir = str(Path(__file__).parent.parent / "backend")
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

REPO_ROOT = Path(__file__).parent.parent

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def mock_auth():
    from identity import Principal, require_principal
    from server import app

    def mock_principal():
        return Principal(id="test-admin", type="bot", name="Test Admin", roles=["admin"])

    app.dependency_overrides[require_principal] = mock_principal
    yield
    app.dependency_overrides.clear()
