from __future__ import annotations

import struct
from io import BytesIO
from pathlib import Path

from n0te2.memory import HeadquartersMemory
from n0te2.version_compare import VersionCompareService


def pcm16_mono_wav(amplitude: int) -> bytes:
    samples = (-amplitude, -(amplitude // 2), 0, amplitude // 2, amplitude)
    data = b"".join(struct.pack("<h", sample) for sample in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 8000 * 2, 2, 16)
    payload = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WAVE" + payload


def float_wav() -> bytes:
    data = struct.pack("<ffff", -0.25, 0.0, 0.25, 0.0)
    fmt = struct.pack("<HHIIHH", 3, 1, 8000, 8000 * 4, 4, 32)
    payload = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(payload)) + b"WAVE" + payload


def ingest(hq: HeadquartersMemory, song_id: str, name: str, payload: bytes):
    return hq.materials.ingest_stream(
        song_id,
        filename=name,
        stream=BytesIO(payload),
        declared_size=len(payload),
    )


def service(hq: HeadquartersMemory) -> VersionCompareService:
    return VersionCompareService(hq.store, hq.materials)


def test_compare_uses_exact_current_parent_and_reports_only_rms_level_delta(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create(tmp_path / "data", "Compare Artist")
    try:
        song = hq.store.create_song("Compare Song")
        first = ingest(hq, song.id, "reference.wav", pcm16_mono_wav(6000))
        second = ingest(hq, song.id, "current.wav", pcm16_mono_wav(12000))
        before_versions = hq.store.versions_for_song(song.id)
        before_learning = hq.learning.episodes_for_song(song.id)
        before_activity = hq.activity.for_song(song.id)
        before_song = hq.store.get_song(song.id)

        comparison = service(hq).prepare()

        assert comparison.status == "READY"
        assert comparison.both_auditionable is True
        assert comparison.current is not None and comparison.reference is not None
        assert comparison.current.version_id == second.version.id
        assert comparison.reference.version_id == first.version.id
        assert comparison.current.status == "AUDITIONABLE_MEASURED"
        assert comparison.reference.status == "AUDITIONABLE_MEASURED"
        assert comparison.current.rms_dbfs is not None
        assert comparison.reference.rms_dbfs is not None
        assert comparison.rms_delta_db is not None
        assert 5.5 < comparison.rms_delta_db < 6.5
        assert any("RMS is not LUFS" in item for item in comparison.limitations)
        assert any("applies no gain" in item for item in comparison.limitations)

        after_song = hq.store.get_song(song.id)
        assert after_song == before_song
        assert hq.store.versions_for_song(song.id) == before_versions
        assert hq.learning.episodes_for_song(song.id) == before_learning
        assert hq.activity.for_song(song.id) == before_activity
    finally:
        hq.close()


def test_auditionable_unmeasured_audio_never_becomes_fake_loudness_evidence(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create(tmp_path / "data", "Compare Artist")
    try:
        song = hq.store.create_song("Compare Song")
        ingest(hq, song.id, "reference.wav", pcm16_mono_wav(6000))
        ingest(hq, song.id, "float-current.wav", float_wav())

        comparison = service(hq).prepare()

        assert comparison.status == "READY"
        assert comparison.current is not None and comparison.reference is not None
        assert comparison.current.auditionable is True
        assert comparison.current.status == "AUDITIONABLE_UNMEASURED"
        assert comparison.current.rms_dbfs is None
        assert comparison.reference.status == "AUDITIONABLE_MEASURED"
        assert comparison.rms_delta_db is None
        assert any("does not invent a level match" in item for item in comparison.limitations)
    finally:
        hq.close()


def test_corrupt_reference_fails_closed_without_hiding_current_audio(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create(tmp_path / "data", "Compare Artist")
    try:
        song = hq.store.create_song("Compare Song")
        reference = ingest(hq, song.id, "reference.wav", pcm16_mono_wav(6000))
        ingest(hq, song.id, "current.wav", pcm16_mono_wav(9000))
        reference.material.path.write_bytes(reference.material.path.read_bytes() + b"tampered")

        comparison = service(hq).prepare()

        assert comparison.status == "PARTIAL"
        assert comparison.current is not None and comparison.current.auditionable is True
        assert comparison.reference is not None
        assert comparison.reference.status == "INTEGRITY_BLOCKED"
        assert comparison.reference.auditionable is False
        assert comparison.rms_delta_db is None
    finally:
        hq.close()


def test_first_version_does_not_create_a_fake_reference(tmp_path: Path) -> None:
    hq = HeadquartersMemory.create(tmp_path / "data", "Compare Artist")
    try:
        song = hq.store.create_song("Compare Song")
        first = ingest(hq, song.id, "only.wav", pcm16_mono_wav(6000))

        comparison = service(hq).prepare()

        assert comparison.status == "NO_REFERENCE_VERSION"
        assert comparison.current is not None
        assert comparison.current.version_id == first.version.id
        assert comparison.reference is None
        assert comparison.reference_version_id is None
    finally:
        hq.close()
