from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from lean_computer_use_mcp.models import Frame


class ImageCache:
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root) if root else Path(tempfile.mkdtemp(prefix="lean-cu-"))
        self.root.mkdir(parents=True, exist_ok=True)

    def store_bytes(self, data: bytes, suffix: str = ".png") -> Path:
        path = self.root / f"{uuid.uuid4().hex}{suffix}"
        path.write_bytes(data)
        return path

    def crop(self, image_path: Path, frame: Frame, output: Path | None = None) -> Path:
        from PIL import Image

        image = Image.open(image_path)
        box = (frame.x, frame.y, frame.x + frame.width, frame.y + frame.height)
        target = output or self.root / f"crop-{uuid.uuid4().hex}.png"
        image.crop(box).save(target, format="PNG")
        return target

    def purge(self) -> None:
        for entry in self.root.glob("*"):
            if entry.is_file():
                entry.unlink()