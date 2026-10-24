"""The hand-written investigation asserts every claim it makes in words.
Regenerating it here fails the test suite if the data stops supporting them."""

from src.config import DB_PATH, ROOT

import pytest


def test_investigation_claims_hold():
    if not DB_PATH.exists():
        pytest.skip("database not built")
    from src import investigate_feb_mar_2018
    investigate_feb_mar_2018.main()
    text = (ROOT / "docs" / "investigation_2018_03.md").read_text()
    assert "14.1% to 19.0%" in text
