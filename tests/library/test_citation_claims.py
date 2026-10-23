import pytest
from library.citation_claims import claim_is_supported

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "claim,expected",
    [
        ('"Rotational power"', True),
        ('"Fabricated quotation"', False),
        ("Bananas orbit quantum oceans", False),
        ("", True),
    ],
)
def test_supported_claims_require_actual_source(claim: str, expected: bool) -> None:
    assert claim_is_supported(claim, len(claim), "Rotational power improves turbine efficiency.") is expected
