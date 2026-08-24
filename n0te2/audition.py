from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class AuditionError(RuntimeError):
    """Local media cannot be auditioned under the bounded consumer contract."""


class UnsupportedAuditionMedia(AuditionError):
    """The verified material is not a browser-auditionable format in this increment."""


class InvalidByteRange(AuditionError):
    """A Range header is malformed or unsupported."""


class UnsatisfiableByteRange(AuditionError):
    """A syntactically valid Range cannot be satisfied for this representation."""


@dataclass(frozen=True)
class AuditionMedia:
    path: Path
    content_type: str
    size_bytes: int


@dataclass(frozen=True)
class ByteRange:
    start: int
    end: int
    total: int

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.total}"


def inspect_audition_media(path: str | Path) -> AuditionMedia:
    media_path = Path(path)
    if not media_path.is_file():
        raise UnsupportedAuditionMedia("audition material is not a regular local file")
    size = media_path.stat().st_size
    if size <= 0:
        raise UnsupportedAuditionMedia("empty material is not auditionable")
    with media_path.open("rb") as handle:
        prefix = handle.read(12)
    if len(prefix) >= 12 and prefix[:4] == b"RIFF" and prefix[8:12] == b"WAVE":
        content_type = "audio/wav"
    elif prefix.startswith(b"ID3") or (
        len(prefix) >= 2
        and prefix[0] == 0xFF
        and (prefix[1] & 0xE0) == 0xE0
        and (prefix[1] & 0x06) != 0
    ):
        content_type = "audio/mpeg"
    else:
        raise UnsupportedAuditionMedia(
            "only signature-verified WAV and MP3 material is auditionable here"
        )
    return AuditionMedia(path=media_path, content_type=content_type, size_bytes=size)


def parse_byte_range(value: str | None, *, size_bytes: int) -> ByteRange | None:
    if size_bytes <= 0:
        raise ValueError("size_bytes must be positive")
    if value is None or not value.strip():
        return None
    text = value.strip()
    if not text.startswith("bytes="):
        raise InvalidByteRange("only byte ranges are supported")
    spec = text[6:].strip()
    if not spec or "," in spec:
        raise InvalidByteRange("exactly one byte range is supported")
    if "-" not in spec:
        raise InvalidByteRange("byte range is missing a dash")
    first, last = (part.strip() for part in spec.split("-", 1))
    if not first:
        if not last.isdigit():
            raise InvalidByteRange("suffix byte range is malformed")
        suffix = int(last)
        if suffix <= 0:
            raise InvalidByteRange("suffix byte range must be positive")
        length = min(suffix, size_bytes)
        return ByteRange(size_bytes - length, size_bytes - 1, size_bytes)
    if not first.isdigit():
        raise InvalidByteRange("byte range start is malformed")
    start = int(first)
    if start >= size_bytes:
        raise UnsatisfiableByteRange("byte range starts beyond the representation")
    if not last:
        return ByteRange(start, size_bytes - 1, size_bytes)
    if not last.isdigit():
        raise InvalidByteRange("byte range end is malformed")
    end = int(last)
    if end < start:
        raise InvalidByteRange("byte range end precedes start")
    return ByteRange(start, min(end, size_bytes - 1), size_bytes)
