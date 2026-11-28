"""A window limits only the model families it names (``LimitWindow.model``); "|" separates several."""

from plainboard_protocol import LimitWindow

from plainboard.services import limits as limits_service


def test_a_full_third_party_window_does_not_hold_a_gemini_turn():
    shared = LimitWindow(name="antigravity_shared_seven_day", utilization=1.0, status="rejected", model="claude|gpt")
    gemini = LimitWindow(name="antigravity_gemini_seven_day", utilization=0.69, model="gemini")
    assert not limits_service.applies(shared, "gemini-4-neon")
    assert limits_service.applies(shared, "claude-sonnet-5-5") and limits_service.applies(shared, "gpt-6.2-astra")
    assert limits_service.fullest([shared, gemini], "gemini-4-neon") == 0.69
    assert limits_service.fullest([shared, gemini], "claude-sonnet-5-5") == 1.0


def test_one_family_and_no_model_keep_their_meaning():
    fable = LimitWindow(name="seven_day_overage_included", utilization=1.0, model="Fable")
    assert limits_service.applies(fable, "claude-fable-5-2") and not limits_service.applies(fable, "claude-opus-5-5")
    plan = LimitWindow(name="seven_day", utilization=0.5)
    assert limits_service.applies(plan, "anything") and limits_service.applies(fable, None)
