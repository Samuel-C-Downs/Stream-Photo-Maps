"""Shared fixtures: synthetic photos with exactly the EXIF each test needs."""

import random
from fractions import Fraction
from pathlib import Path

import pytest
from PIL import Image, ImageDraw
from PIL.TiffImagePlugin import IFDRational

from photomap.indexer import Settings


def _dms(value):
    v = abs(value)
    d = int(v)
    m = int((v - d) * 60)
    s = ((v - d) * 60 - m) * 60
    return (IFDRational(d, 1), IFDRational(m, 1), IFDRational(Fraction(s).limit_denominator(10000)))


def picture(seed, size=(640, 480)):
    """A distinct, detailed image per seed (so fingerprints differ between seeds)."""
    rng = random.Random(seed)
    img = Image.new("RGB", size, tuple(rng.randrange(256) for _ in range(3)))
    draw = ImageDraw.Draw(img)
    for _ in range(25):
        x0, y0 = rng.randrange(size[0]), rng.randrange(size[1])
        x1, y1 = x0 + rng.randrange(40, 300), y0 + rng.randrange(40, 300)
        draw.rectangle([x0, y0, x1, y1], fill=tuple(rng.randrange(256) for _ in range(3)))
    return img


def build_exif(*, lat=None, lon=None, taken=None, offset=None, subsec=None, gps_time=None,
               make="samsung", model="SM-G996U", orientation=None):
    exif = Image.Exif()
    if make:
        exif[271] = make
    if model:
        exif[272] = model
    if orientation:
        exif[274] = orientation
    sub = {}
    if taken:
        sub[36867] = taken
    if offset:
        sub[36881] = offset
    if subsec:
        sub[37521] = subsec
    if sub:
        exif[0x8769] = sub
    if lat is not None:
        gps = {1: "N" if lat >= 0 else "S", 2: _dms(lat), 3: "E" if lon >= 0 else "W", 4: _dms(lon),
               5: b"\x00", 6: IFDRational(1650, 1)}
        if gps_time:
            day, hms = gps_time
            gps[29] = day
            gps[7] = tuple(IFDRational(x, 1) for x in hms)
        exif[0x8825] = gps
    return exif


@pytest.fixture
def make_photo(tmp_path):
    """make_photo(name, seed=..., **exif_fields) -> Path, saved under tmp_path/photos."""
    folder = tmp_path / "photos"
    folder.mkdir(exist_ok=True)

    def _make(name, seed=0, size=(640, 480), fmt="JPEG", quality=90, img=None, **exif_fields):
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        image = img if img is not None else picture(seed, size)
        exif = build_exif(**exif_fields) if exif_fields else Image.Exif()
        kwargs = {"exif": exif.tobytes()} if exif_fields else {}
        if fmt == "JPEG":
            kwargs["quality"] = quality
        image.save(path, fmt, **kwargs)
        return path

    _make.folder = folder
    return _make


@pytest.fixture
def settings(tmp_path):
    return Settings(photo_folder=tmp_path / "photos", data_dir=tmp_path / "data", workers=2)
