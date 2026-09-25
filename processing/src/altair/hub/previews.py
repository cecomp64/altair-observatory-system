"""Previews for the Hub (SPEC §6.6 step 6): an auto-STF stretched JPEG
(long edge 2048 px) and a thumbnail (512 px) from a master (FITS, or XISF
when the `xisf` package is installed)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def load_image(path: str | Path) -> np.ndarray:
    path = Path(path)
    if path.suffix.lower() == ".xisf":
        from xisf import XISF  # optional dependency

        data = XISF(str(path)).read_image(0)
    else:
        from astropy.io import fits

        with fits.open(path) as hdul:
            data = next(h.data for h in hdul if h.data is not None)
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 3 and data.shape[0] in (1, 3):  # FITS planes first
        data = np.moveaxis(data, 0, -1)
    return np.squeeze(data)


def auto_stf(data: np.ndarray, target_background: float = 0.25, shadows_clip: float = -2.8) -> np.ndarray:
    """PixInsight's ScreenTransferFunction auto-stretch, per channel, linked."""
    finite = data[np.isfinite(data)]
    lo, hi = float(finite.min()), float(finite.max())
    x = (data - lo) / (hi - lo) if hi > lo else np.zeros_like(data)
    x = np.nan_to_num(x)
    median = float(np.median(x))
    mad = float(np.median(np.abs(x - median))) * 1.4826
    c0 = min(max(median + shadows_clip * mad, 0.0), 1.0)
    m = _mtf(target_background, median - c0) if median > c0 else 0.5
    y = np.clip((x - c0) / (1 - c0), 0, 1) if c0 < 1 else np.zeros_like(x)
    return _mtf(m, y)


def _mtf(m: float, x):
    """Midtones transfer function."""
    return ((m - 1) * x) / ((2 * m - 1) * x - m)


def render(source: str | Path, preview_path: str | Path, thumb_path: str | Path, *, long_edge: int = 2048, thumb: int = 512,
           quality: int = 85) -> tuple[Path, Path]:
    stretched = auto_stf(load_image(source))
    pixels = (np.clip(stretched, 0, 1) * 255).astype(np.uint8)
    image = Image.fromarray(pixels, mode="RGB" if pixels.ndim == 3 else "L")
    outputs = []
    for path, edge in ((Path(preview_path), long_edge), (Path(thumb_path), thumb)):
        path.parent.mkdir(parents=True, exist_ok=True)
        copy = image.copy()
        copy.thumbnail((edge, edge))
        copy.save(path, "JPEG", quality=quality)
        outputs.append(path)
    return outputs[0], outputs[1]
