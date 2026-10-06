"""Write song details and the cover into the finished file."""
from mutagen.id3 import APIC, ID3, TALB, TCON, TIT2, TPE1, TPE2, TRCK, TYER, COMM, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover


def write(path, meta, cover_jpeg=None):
    fmt = str(path).rsplit(".", 1)[-1].lower()
    if fmt == "mp3":
        _mp3(path, meta, cover_jpeg)
    elif fmt == "m4a":
        _m4a(path, meta, cover_jpeg)


def _mp3(path, m, cover):
    try:
        tags = ID3(path)
        tags.delete()
    except ID3NoHeaderError:
        pass
    tags = ID3()
    # ID3v2.3 is what Windows, older players and Spotify read most reliably;
    # mutagen stores the Thai text as UTF-16 in v2.3.
    tags.add(TIT2(encoding=1, text=m["title"]))
    tags.add(TPE1(encoding=1, text=m["artist"]))
    if m.get("album"):
        tags.add(TALB(encoding=1, text=m["album"]))
    tags.add(TPE2(encoding=1, text=m.get("album_artist") or m["artist"]))
    if m.get("track"):
        tags.add(TRCK(encoding=1, text=str(m["track"])))
    if m.get("year"):
        tags.add(TYER(encoding=1, text=str(m["year"])))
    if m.get("genre"):
        tags.add(TCON(encoding=1, text=m["genre"]))
    if m.get("comment"):
        tags.add(COMM(encoding=1, lang="eng", desc="", text=m["comment"]))
    if cover:
        # exactly one picture, type 3 = front cover
        tags.add(APIC(encoding=0, mime="image/jpeg", type=3, desc="", data=cover))
    tags.save(path, v2_version=3, v1=0)


def _m4a(path, m, cover):
    f = MP4(path)
    f.delete()
    f["\xa9nam"] = [m["title"]]
    f["\xa9ART"] = [m["artist"]]
    f["aART"] = [m.get("album_artist") or m["artist"]]
    if m.get("album"):
        f["\xa9alb"] = [m["album"]]
    if m.get("track"):
        try:
            f["trkn"] = [(int(str(m["track"]).split("/")[0]), 0)]
        except ValueError:
            pass
    if m.get("year"):
        f["\xa9day"] = [str(m["year"])]
    if m.get("genre"):
        f["\xa9gen"] = [m["genre"]]
    if m.get("comment"):
        f["\xa9cmt"] = [m["comment"]]
    if cover:
        f["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
    f.save()


def read(path):
    """Short summary of what a file carries, for the result card and tests."""
    fmt = str(path).rsplit(".", 1)[-1].lower()
    if fmt == "mp3":
        t = ID3(path)
        pics = t.getall("APIC")
        return {"version": f"2.{t.version[1]}", "title": str(t.get("TIT2", "")),
                "artist": str(t.get("TPE1", "")), "album": str(t.get("TALB", "")),
                "album_artist": str(t.get("TPE2", "")), "pictures": len(pics),
                "picture_bytes": len(pics[0].data) if pics else 0}
    f = MP4(path)
    covr = f.get("covr") or []
    return {"title": (f.get("\xa9nam") or [""])[0], "artist": (f.get("\xa9ART") or [""])[0],
            "album": (f.get("\xa9alb") or [""])[0], "album_artist": (f.get("aART") or [""])[0],
            "pictures": len(covr), "picture_bytes": len(covr[0]) if covr else 0}
