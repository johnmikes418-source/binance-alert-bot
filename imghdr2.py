# imghdr2.py - drop-in replacement for Python 3.13+
import pathlib
from typing import Optional

def what(file, h: Optional[bytes] = None):
    path = pathlib.Path(file)
    if h is None:
        try:
            with open(path, "rb") as f:
                h = f.read(32)
        except Exception:
            return None

    if h.startswith(b"\xff\xd8"):
        return "jpeg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None
