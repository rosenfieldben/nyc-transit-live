"""Damaged GTFS archives, built from good ones, for the static loaders' tests.

ONE COPY OF EACH DAMAGE, shared by the five loaders' test files, because what each
shape raises is the point of the tests that use it and a copy per file is a copy that
drifts (test_static_data.py's shapes helper and the a02b script's fixture are where
these were first worked out).

Every helper takes and returns the bytes of a whole archive, so a test can plant the
result as a cache, publish it, or hand it to a validator.
"""

from __future__ import annotations

import io
import zipfile


def recompressed(archive: bytes, member: str, compression: int) -> bytes:
    """The same archive with `member` compressed by `compression` and every other member
    as it was. The builders write members STORED, which has no compressed stream to
    damage, so a test that wants one asks for it here."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(buf, "w") as target:
        for info in source.infolist():
            body = source.read(info.filename)
            kind = compression if info.filename == member else info.compress_type
            target.writestr(info.filename, body, compress_type=kind)
    return buf.getvalue()


def _payload_offset(raw: bytes | bytearray, member: str) -> int:
    """Where `member`'s compressed bytes begin. Located through header_offset, not by
    searching for the name: the name appears again in the central directory, and a byte
    flipped there makes the whole archive unopenable, which is a different failure."""
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as zf:
        offset = zf.getinfo(member).header_offset
    name_len = int.from_bytes(raw[offset + 26 : offset + 28], "little")
    extra_len = int.from_bytes(raw[offset + 28 : offset + 30], "little")
    return offset + 30 + name_len + extra_len


def damaged_stream(archive: bytes, member: str, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    """`member` compressed by `compression` with its compressed stream damaged, its CRC and
    sizes untouched, so the archive opens and lists and only READING the member raises what
    that decompressor raises: zlib.error for DEFLATE, a plain OSError (no errno) for bzip2,
    LZMAError for lzma.

    DEFLATE's first block header is set to the block type RFC 1951 reserves (11), which
    inflate refuses at once; a flipped data byte in a stream this short is as likely to
    reach the CRC check first and raise BadZipFile instead. The other two have a byte of
    their stream flipped past their own headers."""
    raw = bytearray(recompressed(archive, member, compression))
    payload = _payload_offset(raw, member)
    if compression == zipfile.ZIP_DEFLATED:
        raw[payload] |= 0b110
    else:
        raw[payload + 10] ^= 0xFF
    return bytes(raw)


def rot_after_the_check(monkeypatch, module, path, rotted: bytes) -> None:
    """Make `module`'s cached-archive check pass on the good bytes at `path` and then
    replace them with `rotted`, once.

    THE ONE WAY A LOAD CAN MEET BYTES ITS CHECK DID NOT SEE, and so the honest way to
    reach a residual arm: the check runs the loader's gate over the cache and calls it
    usable, and the cache changes before the load reads it (rot, a torn write, another
    process). Planting the rotted bytes before the check would test the check instead,
    and since the check became the publication gate it refuses them there. One-shot, so
    the next attempt meets the real check and whatever is on disk by then."""
    real = module.cached_archive_is_usable
    armed = {"left": 1}

    async def check_then_rot(target, validate):
        usable = await real(target, validate)
        if armed["left"] and target == path:
            armed["left"] = 0
            path.write_bytes(rotted)
        return usable

    monkeypatch.setattr(module, "cached_archive_is_usable", check_then_rot)


def undecodable_deep_in(archive: bytes, member: str, *, filler: int = 4000) -> bytes:
    """`member` with `filler` copies of its last data row appended and then one more row
    carrying a 0xFF byte, so the byte sits far past the first chunk a text reader decodes
    (8 KB) and past any read that stops at the first rows. Only a parse of the whole
    member finds it. The archive is otherwise byte-for-byte the input's members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(archive)) as source, zipfile.ZipFile(buf, "w") as target:
        for info in source.infolist():
            body = source.read(info.filename)
            if info.filename == member:
                lines = body.rstrip(b"\n").split(b"\n")
                last = lines[-1]
                body = b"\n".join([*lines, *([last] * filler), last + b"\xff"]) + b"\n"
                assert len(body) - body.index(b"\xff") < 64 and body.index(b"\xff") > 8192
            target.writestr(info.filename, body, compress_type=info.compress_type)
    return buf.getvalue()
