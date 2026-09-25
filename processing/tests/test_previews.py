import numpy as np
from astropy.io import fits
from PIL import Image

from altair.hub.previews import auto_stf, render


def test_auto_stf_puts_the_background_near_a_quarter():
    data = np.random.default_rng(0).normal(1000, 5, (200, 300))
    data[100, 150] = 60000  # a star
    stretched = auto_stf(data)
    assert 0.15 < np.median(stretched) < 0.35
    assert stretched.max() <= 1.0 and stretched.min() >= 0.0


def test_render_writes_a_preview_and_a_thumbnail(tmp_path):
    source = tmp_path / "master.fits"
    fits.writeto(source, np.random.default_rng(1).normal(1000, 5, (3, 400, 600)).astype(np.float32))
    preview, thumb = render(source, tmp_path / "p.jpg", tmp_path / "t.jpg", long_edge=300, thumb=64)
    assert Image.open(preview).size == (300, 200)
    assert max(Image.open(thumb).size) == 64
    assert preview.read_bytes()[:3] == b"\xff\xd8\xff"
