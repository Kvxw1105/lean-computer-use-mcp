from __future__ import annotations

import hashlib

from lean_computer_use_mcp.models import StateSnapshot


def image_fingerprint(
    image: bytes, rect: tuple[int, int, int, int] | None = None
) -> str:
    """Low-cost perceptual hash of a screenshot for stale-state gating.

    Used only when the UIA tree is trivial (self-drawn apps such as JianYing):
    the 9x8 grayscale dHash grid is robust to small pixel noise (cursor blink,
    antialiasing) while still catching structural changes (new panel, content
    swap). The source dimensions are included so a window resize changes the
    fingerprint. ``rect`` (left, top, right, bottom screen coordinates) is
    folded in when the client provides it, so a window that is *moved* (same
    pixels, same size) still changes the fingerprint: screenshot-pixel
    coordinates stop being valid when the window moves. The hash is compared
    locally only: it never leaves the machine, never appears in responses,
    and never reaches metrics. Returns ``""`` when the image cannot be
    decoded so callers can fall back to the tree fingerprint alone.
    """
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(image)) as img:
            gray = img.convert("L")
            size = gray.size
            tiny = gray.resize((9, 8), Image.Resampling.LANCZOS)
        pixels = tiny.tobytes()  # "L" mode: one byte per pixel
        bits: list[str] = []
        for row in range(8):
            base = row * 9
            for col in range(8):
                bits.append("1" if pixels[base + col] > pixels[base + col + 1] else "0")
        digest = hashlib.sha256("".join(bits).encode("ascii")).hexdigest()[:16]
        prefix = f"{size[0]}x{size[1]}"
        if rect is not None:
            prefix += f"@{rect[0]},{rect[1]},{rect[2]},{rect[3]}"
        return f"{prefix}:{digest}"
    except Exception:  # noqa: BLE001 - unreadable image => fingerprint unavailable
        return ""


def fingerprint(snapshot: StateSnapshot) -> str:
    lines = [f"{snapshot.app}|{snapshot.window_title}|{snapshot.focused_element}"]
    for node in snapshot.controls:
        frame = node.frame.to_dict() if node.frame else {}
        lines.append(
            "|".join(
                [
                    node.index,
                    node.role,
                    node.name,
                    str(node.value),
                    str(node.actions),
                    str(frame),
                ]
            )
        )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]