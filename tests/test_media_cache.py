"""Image cache: byte store, frame crops, purge. Platform-neutral (Pillow)."""

from __future__ import annotations

from PIL import Image

from lean_computer_use_mcp.media.cache import ImageCache
from lean_computer_use_mcp.models import Frame


def _png_bytes(size: tuple[int, int] = (32, 24)) -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_store_bytes_writes_file_with_suffix(tmp_path):
    cache = ImageCache(str(tmp_path))
    path = cache.store_bytes(_png_bytes(), suffix=".png")
    assert path.parent == tmp_path
    assert path.suffix == ".png"
    assert path.read_bytes().startswith(b"\x89PNG")


def test_crop_saves_requested_frame(tmp_path):
    cache = ImageCache(str(tmp_path))
    path = cache.crop(cache.store_bytes(_png_bytes((32, 24))), Frame(0, 0, 8, 6))
    with Image.open(path) as image:
        assert image.size == (8, 6)


def test_crop_honors_explicit_output(tmp_path):
    cache = ImageCache(str(tmp_path))
    output = tmp_path / "out.png"
    cache.crop(cache.store_bytes(_png_bytes((16, 16))), Frame(2, 2, 4, 4), output)
    assert output.exists()
    with Image.open(output) as image:
        assert image.size == (4, 4)


def test_purge_removes_all_files(tmp_path):
    cache = ImageCache(str(tmp_path))
    cache.store_bytes(_png_bytes())
    cache.store_bytes(_png_bytes())
    assert len(list(tmp_path.iterdir())) == 2
    cache.purge()
    assert list(tmp_path.iterdir()) == []
