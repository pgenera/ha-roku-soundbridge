"""Display control utilities for the Roku SoundBridge."""

from __future__ import annotations

import asyncio
import logging
import os

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

_LOGGER = logging.getLogger(__name__)


def get_font_path(font_name: str) -> str:
    """Get the path to a bundled font."""
    return os.path.join(os.path.dirname(__file__), "fonts", font_name)


def _image_to_lines(img, width: int, height: int) -> list[str]:
    """Convert a Pillow image to sketch line commands."""
    commands = []
    pixels = img.load()
    for x in range(width):
        in_segment = False
        segment_start = 0
        for y in range(height):
            # 0 is black (off), 1 (or 255) is white (on) in mode "1"
            is_on = pixels[x, y] > 0
            if is_on and not in_segment:
                in_segment = True
                segment_start = y
            elif not is_on and in_segment:
                in_segment = False
                commands.append(f"line {x} {segment_start} {x} {y - 1}")
        if in_segment:
            commands.append(f"line {x} {segment_start} {x} {height - 1}")
    return commands


def render_text_to_commands(
    text: str,
    size: int = 32,
    x: int = 0,
    y: int = 0,
    font_path: str | None = None,
    anchor: str | None = None,
    width: int = 512,
    height: int = 32,
) -> list[str]:
    """Render text using Pillow and return sketch commands."""
    if Image is None:
        _LOGGER.error("Pillow is not installed. Cannot render text.")
        return []

    if font_path is None:
        font_path = get_font_path("Roboto-Regular.ttf")

    try:
        font = ImageFont.truetype(font_path, size)
    except Exception as err:
        _LOGGER.warning("Failed to load font %s, falling back: %s", font_path, err)
        font = ImageFont.load_default()

    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)

    # Use specified anchor or calculate default
    if anchor is None:
        # Default to centered if y is 0, otherwise top-left
        if y == 0:
            anchor = "lm"
            draw_y = height // 2
        else:
            anchor = "lt"
            draw_y = y
    else:
        draw_y = y

    draw.text((x, draw_y), text, font=font, fill=1, anchor=anchor)

    return _image_to_lines(img, width, height)


def render_icon_to_commands(
    icon_codepoint: str,
    size: int = 32,
    x: int = 0,
    y: int = 0,
    anchor: str = "lm",
    width: int = 512,
    height: int = 32,
) -> list[str]:
    """Render an MDI icon using Pillow and return sketch commands."""
    if Image is None:
        _LOGGER.error("Pillow is not installed. Cannot render icon.")
        return []

    font_path = get_font_path("materialdesignicons-webfont.ttf")
    try:
        font = ImageFont.truetype(font_path, size)
    except Exception as err:
        _LOGGER.warning("Failed to load MDI font: %s", err)
        return []

    img = Image.new("1", (width, height), 0)
    draw = ImageDraw.Draw(img)

    # Icons are almost always best vertically centered
    draw_y = y if anchor != "lm" else height // 2
    draw.text((x, draw_y), icon_codepoint, font=font, fill=1, anchor=anchor)

    return _image_to_lines(img, width, height)


async def async_send_sketch_commands(host: str, port: int, commands: list[str]) -> bool:
    """Send a list of commands to the SoundBridge sketch sub-shell on port 4444."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=5
        )

        # Wait for SoundBridge> prompt
        # We use a loop to consume the banner
        buf = b""
        while b"SoundBridge> " not in buf:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
            if not chunk:
                break
            buf += chunk

        _LOGGER.debug("Entering sketch mode on %s:%d", host, port)
        writer.write(b"sketch\r\n")
        await writer.drain()

        # Wait for sketch> prompt
        buf = b""
        while b"sketch> " not in buf:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=2)
            if not chunk:
                break
            buf += chunk

        # Send commands in batches to avoid buffer overflow
        chunk_size = 20
        for i in range(0, len(commands), chunk_size):
            batch = "\r\n".join(commands[i : i + chunk_size]) + "\r\n"
            writer.write(batch.encode())
            await writer.drain()
            # Minimal sleep to let the device parse
            await asyncio.sleep(0.02)

        # Finish without quitting so the sketch remains on the display
        writer.close()
        await writer.wait_closed()
    except (TimeoutError, ConnectionRefusedError, OSError) as err:
        _LOGGER.error("Failed to send sketch commands to %s:%d: %s", host, port, err)
        return False
    else:
        return True


def parse_display_bitmap(data: bytes, width: int = 512, height: int = 32) -> list[str]:
    """Parse raw display data into a list of strings representing rows (1s and 0s)."""
    lines = []
    bytes_per_row = width // 8
    for y in range(height):
        line = []
        for x in range(width):
            byte_idx = y * bytes_per_row + (x // 8)
            bit_idx = 7 - (x % 8)
            if byte_idx < len(data):
                is_on = (data[byte_idx] >> bit_idx) & 1
                line.append("1" if is_on else "0")
            else:
                line.append("0")
        lines.append("".join(line))
    return lines


def bitmap_to_lines(
    bitmap_data: bytes, width: int = 512, height: int = 32
) -> list[str]:
    # SoundBridge Large VFD format: columns of 4 bytes (32 bits)
    # Byte 0: bits 0-7 (top to bottom)
    # Byte 1: bits 8-15
    # Byte 2: bits 16-23
    # Byte 3: bits 24-31

    commands = ["clear"]

    for x in range(width):
        # Extract 32 bits for this column
        offset = x * 4
        if offset + 4 > len(bitmap_data):
            break

        # Combine 4 bytes into one 32-bit integer
        col_val = (
            bitmap_data[offset]
            | (bitmap_data[offset + 1] << 8)
            | (bitmap_data[offset + 2] << 16)
            | (bitmap_data[offset + 3] << 24)
        )

        if col_val == 0:
            continue

        # Find segments of consecutive bits
        in_segment = False
        segment_start = 0

        for y in range(height):
            bit_set = (col_val >> y) & 1
            if bit_set and not in_segment:
                in_segment = True
                segment_start = y
            elif not bit_set and in_segment:
                in_segment = False
                commands.append(f"line {x} {segment_start} {x} {y - 1}")

        if in_segment:
            commands.append(f"line {x} {segment_start} {x} {height - 1}")

    return commands
