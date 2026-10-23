import pytest
from library.citation_claims import claim_is_supported

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("claim", "evidence", "expected"),
    [
        ('"Rotational power"', "Rotational power improves turbine efficiency.", True),
        ('"Fabricated quotation"', "Rotational power improves turbine efficiency.", False),
        ("Bananas orbit quantum oceans", "Rotational power improves turbine efficiency.", False),
        ("This function is not safe", "This function is safe.", False),
        ("This function is safe", "This function is not safe.", False),
        ("This function is not safe", "This function is not safe.", True),
        ("Bananas improve turbine efficiency", "Rotational power improves turbine efficiency.", False),
        ("Rotational power improves turbine efficiency", "Rotational power improves turbine efficiency.", True),
        (
            'Rotational power prevents safety using "RotorX"',
            "RotorX is a turbine. Rotational power improves efficiency.",
            False,
        ),
        (
            "Bananas improve turbine efficiency",
            "Bananas improve turbines. Efficiency improves on each cycle.",
            False,
        ),
        ("", "Rotational power improves turbine efficiency.", True),
    ],
)
def test_supported_claims_require_actual_source(claim: str, evidence: str, expected: bool) -> None:
    assert claim_is_supported(claim, len(claim), evidence) is expected
