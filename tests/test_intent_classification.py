"""Tests for _classify_intent and _text_matches_keyword_list in coach agent."""

from app.agents.coach.agent import (
    _classify_intent,
    _text_matches_keyword_list,
    _is_degenerate_response,
)


class TestFastExactWhitelist:
    def test_greeting_hi(self):
        assert _classify_intent("hi") == "fast"

    def test_greeting_hello(self):
        assert _classify_intent("hello") == "fast"

    def test_greeting_ok(self):
        assert _classify_intent("ok") == "fast"

    def test_greeting_thanks(self):
        assert _classify_intent("thanks") == "fast"

    def test_greeting_cam_on_no_diacritics(self):
        assert _classify_intent("cam on") == "fast"

    def test_greeting_chao_no_diacritics(self):
        assert _classify_intent("chao") == "fast"

    def test_greeting_with_diacritics(self):
        assert _classify_intent("cảm ơn") == "fast"

    def test_emoji_thumbsup(self):
        assert _classify_intent("👍") == "fast"


class TestStandardKeywordRouting:
    def test_lich_trinh_no_diacritics(self):
        """Core bug: 'lich trinh tap luyen' must route to standard."""
        assert (
            _classify_intent("lich trinh tap luyen cua toi tuan toi chi tiet")
            == "standard"
        )

    def test_lich_with_diacritics(self):
        assert _classify_intent("lịch trình tập luyện tuần tới") == "standard"

    def test_training_schedule_english(self):
        assert _classify_intent("what is my training schedule next week") == "standard"

    def test_analyze_run(self):
        assert _classify_intent("phân tích bài chạy hôm qua") == "standard"

    def test_ke_hoach_no_diacritics(self):
        assert _classify_intent("ke hoach tap luyen") == "standard"

    def test_muc_tieu(self):
        assert _classify_intent("mục tiêu của tôi là gì") == "standard"

    def test_tuan_toi_no_diacritics(self):
        assert _classify_intent("tuan toi tap gi") == "standard"

    def test_pace_english(self):
        assert _classify_intent("what pace should I run at") == "standard"

    def test_acwr(self):
        assert _classify_intent("acwr của tôi là bao nhiêu") == "standard"

    def test_km(self):
        assert _classify_intent("tổng km tuần này") == "standard"

    def test_giao_an(self):
        assert _classify_intent("giáo án tháng 5") == "standard"


class TestLongTextDefaultsToStandard:
    def test_long_text_over_80_chars(self):
        long_text = "a" * 81
        assert _classify_intent(long_text) == "standard"

    def test_exactly_80_chars_unknown(self):
        # 80 chars of unrecognized text — hits keyword check, should default standard
        assert _classify_intent("x" * 80) == "standard"


class TestSafeDefault:
    def test_unknown_short_text(self):
        """Unknown short text not in whitelist → standard (safe default)."""
        assert _classify_intent("xyzzy nonsense") == "standard"

    def test_empty_string(self):
        assert _classify_intent("") == "standard"

    def test_whitespace_only(self):
        assert _classify_intent("   ") == "standard"

    def test_number_only(self):
        assert _classify_intent("42") == "standard"


class TestDiacriticFolding:
    def test_folded_tap_matches_tap_with_diacritics(self):
        assert _text_matches_keyword_list("tap luyen", ("tập",))

    def test_folded_lich_matches_keyword(self):
        assert _text_matches_keyword_list("lich trinh", ("lịch",))

    def test_en_keyword_exact(self):
        assert _text_matches_keyword_list("show my schedule", ("schedule",))


class TestDegenerateResponse:
    def test_none_is_degenerate(self):
        assert _is_degenerate_response(None)

    def test_empty_string_is_degenerate(self):
        assert _is_degenerate_response("")

    def test_short_string_is_not_degenerate(self):
        assert not _is_degenerate_response("ok")

    def test_normal_reply_not_degenerate(self):
        assert not _is_degenerate_response("Đây là lịch tập luyện tuần tới của bạn!")
