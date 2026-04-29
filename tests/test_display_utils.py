"""Tests for the Roku SoundBridge display utilities."""

from custom_components.roku_soundbridge.display import bitmap_to_lines


def test_bitmap_to_lines_empty():
    """Test that an empty bitmap returns just 'clear'."""
    data = b"\x00" * 2048
    lines = bitmap_to_lines(data)
    assert lines == ["clear"]


def test_bitmap_to_lines_single_pixel():
    """Test that a single pixel results in a 1-pixel line."""
    # First column, first bit (top-left)
    data = bytearray(2048)
    data[0] = 0x01
    lines = bitmap_to_lines(data)
    assert "line 0 0 0 0" in lines


def test_bitmap_to_lines_full_column():
    """Test that a full column results in one vertical line."""
    data = bytearray(2048)
    # First column: 4 bytes of 0xFF
    data[0] = 0xFF
    data[1] = 0xFF
    data[2] = 0xFF
    data[3] = 0xFF
    lines = bitmap_to_lines(data)
    assert "line 0 0 0 31" in lines


def test_bitmap_to_lines_segments():
    """Test that multiple segments in a column are correctly identified."""
    data = bytearray(2048)
    # First column: 0x01 (bit 0), 0x04 (bit 2)
    # bits 0 and 2 are set, bit 1 is clear
    data[0] = 0x05
    lines = bitmap_to_lines(data)
    assert "line 0 0 0 0" in lines
    assert "line 0 2 0 2" in lines
    assert len(lines) == 3  # clear + 2 lines


def test_bitmap_to_lines_spanning_bytes():
    """Test segments that span across byte boundaries."""
    data = bytearray(2048)
    # Bits 7 and 8 set
    data[0] = 0x80  # bit 7
    data[1] = 0x01  # bit 8
    lines = bitmap_to_lines(data)
    assert "line 0 7 0 8" in lines
