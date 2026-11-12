"""Camera mode: while the web page's camera is on, Claude sees what it sees.

The page sends a small picture about once a second. Each question the user asks goes to Claude with the
newest picture, so it sees what they're pointing at right now, a bit like Gemini Live.
"""
import base64
import time

FRAME_MAX_AGE = 5  # Seconds. An older frame means the page stopped sending, so camera mode is over.
MAX_FRAME_BYTES = 2_000_000

_frame: bytes | None = None
_frame_time = 0.0


def save_frame(jpeg: bytes) -> None:
    """Keep the newest camera frame. Called from the web server's thread."""
    global _frame, _frame_time
    _frame, _frame_time = jpeg, time.time()


def camera_off() -> None:
    global _frame
    _frame = None


def camera_active() -> bool:
    return _frame is not None and time.time() - _frame_time < FRAME_MAX_AGE


def image_block() -> dict | None:
    """The newest frame as an image for Claude, or None if the camera is off"""
    frame = _frame
    if frame is None or not camera_active():
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(frame).decode()}}
