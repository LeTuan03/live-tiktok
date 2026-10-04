"""Tests for P0 fixes — stage direction stripping and safety nets."""

import pytest
from app.services.voice_service import strip_stage_directions


class TestStripStageDirections:
    """Bug #1: TTS must NEVER read stage directions aloud."""

    # --- Parenthesized cues (the most common pattern) ---
    def test_removes_parenthesized_cue(self):
        text = "Chào cả nhà (giơ sản phẩm lại gần camera), giá chỉ còn 199k!"
        result = strip_stage_directions(text)
        assert "(giơ sản phẩm lại gần camera)" not in result
        assert "Chào cả nhà" in result
        assert "giá chỉ còn 199k!" in result

    def test_removes_smile_cue(self):
        text = "Sản phẩm này tuyệt vời lắm (cười tươi) nha các chị!"
        result = strip_stage_directions(text)
        assert "(cười tươi)" not in result
        assert "Sản phẩm này tuyệt vời lắm" in result

    def test_removes_multiple_parenthesized(self):
        text = "(vẫy tay) Chào mọi người! (giơ sản phẩm) Đây là kem dưỡng siêu hot (cười)"
        result = strip_stage_directions(text)
        assert "(" not in result
        assert ")" not in result
        assert "Chào mọi người!" in result

    # --- Square bracket cues ---
    def test_removes_bracket_cue(self):
        text = "[cười tươi] Hôm nay có deal sốc!"
        result = strip_stage_directions(text)
        assert "[cười tươi]" not in result
        assert "Hôm nay có deal sốc!" in result

    # --- Asterisk cues ---
    def test_removes_asterisk_cue(self):
        text = "Giá chỉ còn *nhấn mạnh* một trăm chín nghìn!"
        result = strip_stage_directions(text)
        assert "*nhấn mạnh*" not in result
        assert "Giá chỉ còn" in result
        assert "một trăm chín nghìn!" in result

    # --- Mixed patterns ---
    def test_removes_all_pattern_types(self):
        text = "[cười tươi] Hôm nay (giơ sản phẩm) có deal sốc *nhấn mạnh* nha!"
        result = strip_stage_directions(text)
        assert "[" not in result
        assert "(" not in result
        assert "*" not in result
        assert "Hôm nay" in result
        assert "có deal sốc" in result
        assert "nha!" in result

    # --- Preserves normal text ---
    def test_preserves_normal_text(self):
        text = "Chào các chị, hôm nay shop mình giảm giá 50% toàn bộ sản phẩm!"
        result = strip_stage_directions(text)
        assert result == text

    def test_empty_string(self):
        assert strip_stage_directions("") == ""

    def test_only_cues(self):
        text = "(cười) [vẫy tay] *nhấn mạnh*"
        result = strip_stage_directions(text)
        assert result == ""

    # --- Collapse spaces ---
    def test_collapses_multiple_spaces(self):
        text = "Chào   (cue)   mọi người"
        result = strip_stage_directions(text)
        # Should have single spaces, not triple
        assert "  " not in result
        assert "Chào mọi người" in result

    # --- Exact examples from bug report #1 ---
    def test_exact_bug1_example_product_hold(self):
        """The exact text from bug #1 dẫn chứng."""
        text = "giá chỉ còn (giơ sản phẩm lại gần camera) 199k"
        result = strip_stage_directions(text)
        assert result == "giá chỉ còn 199k"

    def test_exact_bug1_example_smile(self):
        text = "sản phẩm tuyệt vời (cười tươi) nha các chị"
        result = strip_stage_directions(text)
        assert result == "sản phẩm tuyệt vời nha các chị"
