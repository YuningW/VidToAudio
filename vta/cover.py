"""Cover art: any picture in, a square Spotify-friendly JPEG out.

What gets written, and why:
  * square: Spotify and every music app show covers as squares; a portrait
    picture gets cropped by the app somewhere you didn't choose.
  * baseline JPEG, not progressive, not PNG: the most widely readable form of
    embedded art.
  * sRGB, no colour profile, no EXIF: no colour shifts, no rotated covers.
  * 640 x 640 by default: the largest size Spotify itself serves for album
    art, and far below the sizes reported to stop local-file art from showing.
"""
import io
import uuid
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter, ImageOps

from . import paths

MAX_SOURCE = 4000


def _to_srgb(img):
    icc = img.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            img = ImageCms.profileToProfile(img, src, dst, outputMode="RGB")
        except Exception:
            pass
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (0, 0, 0))
        bg.paste(img, mask=img.split()[-1])
        return bg
    return img.convert("RGB")


def store(data, name="cover"):
    """Save an uploaded picture, upright and in sRGB. Returns its record."""
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception:
        raise ValueError("That file isn't a picture I can open (try JPG, PNG, WEBP or HEIC exported as JPG).")
    img = _to_srgb(img)
    if max(img.size) > MAX_SOURCE:
        img.thumbnail((MAX_SOURCE, MAX_SOURCE), Image.LANCZOS)
    cid = uuid.uuid4().hex[:12]
    d = paths.COVERS / cid
    d.mkdir(parents=True, exist_ok=True)
    img.save(d / "source.jpg", "JPEG", quality=95)
    return {"id": cid, "width": img.width, "height": img.height, "name": name}


def source_path(cid):
    if not cid or not cid.isalnum():
        raise ValueError("Unknown cover.")
    p = paths.COVERS / cid / "source.jpg"
    if not p.exists():
        raise ValueError("That cover is gone. Drop it in again.")
    return p


def make_square(cid, crop=None, mode="crop", size=640):
    """crop = {x, y, size} in source pixels. mode 'crop' or 'fit' (blurred fill)."""
    img = Image.open(source_path(cid)).convert("RGB")
    size = int(min(max(size, 300), 3000))
    if mode == "fit":
        bg = ImageOps.fit(img, (size, size), Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(size / 24))
        bg = Image.blend(bg, Image.new("RGB", bg.size, (0, 0, 0)), 0.25)
        fg = img.copy()
        fg.thumbnail((size, size), Image.LANCZOS)
        bg.paste(fg, ((size - fg.width) // 2, (size - fg.height) // 2))
        out = bg
    else:
        if crop:
            s = max(1, int(min(crop["size"], img.width, img.height)))
            x = int(min(max(0, crop["x"]), img.width - s))
            y = int(min(max(0, crop["y"]), img.height - s))
            img = img.crop((x, y, x + s, y + s))
        else:
            img = ImageOps.fit(img, (min(img.size),) * 2, Image.LANCZOS)
        out = img.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=90, optimize=True, progressive=False, subsampling="4:2:0")
    return buf.getvalue()


def from_file(path):
    return store(Path(path).read_bytes(), name="Video thumbnail")
