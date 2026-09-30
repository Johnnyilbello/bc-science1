import pytest

from bc_science.config import resolve_ask_profile


def test_auto_fast_routes_to_turbo():
    assert resolve_ask_profile("auto", deep=False) == "turbo"


def test_auto_deep_routes_to_standard():
    assert resolve_ask_profile("auto", deep=True) == "standard"


@pytest.mark.parametrize("profile", ["turbo", "standard", "quality"])
def test_explicit_profile_is_preserved(profile: str):
    assert resolve_ask_profile(profile, deep=False) == profile


def test_invalid_profile_is_rejected():
    with pytest.raises(ValueError):
        resolve_ask_profile("fastest", deep=False)
