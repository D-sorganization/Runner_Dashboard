from backend.demo_mode import demo_api_block


def test_demo_mode_blocks_all_api_access() -> None:
    assert demo_api_block("/api/runners", enabled=True).status_code == 403
    assert demo_api_block("/api/runners/1", enabled=True).status_code == 403
    assert demo_api_block("/api/runners", enabled=False) is None


def test_demo_mode_does_not_block_non_api_posts() -> None:
    assert demo_api_block("/login", enabled=True) is None
