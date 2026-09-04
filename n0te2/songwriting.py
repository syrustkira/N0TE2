from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .evidence import EvidenceClaim
from .lineage import LineageStore, NotFoundError, ValidationError
from .session import SessionItem, SessionMemory, SongSession

SONGWRITING_ASPECTS = (
    "LYRICS",
    "TOPLINE",
    "MELODY",
    "PHRASING",
    "TAKE_COMP",
    "LYRIC_ALIGNMENT",
    "PITCH_TIMING",
    "DOUBLES",
    "HARMONIES",
    "AD_LIBS",
    "PERFORMANCE",
    "VOCAL_PRODUCTION",
)
SONGWRITING_ENTRY_KINDS = (
    "MARK",
    "OBSERVATION",
    "DECISION",
    "REJECTED_IDEA",
    "UNRESOLVED",
)
SONGWRITING_SOURCE_KIND = "USER_DECLARED"
MAX_SONGWRITING_TEXT_CHARS = 12_000
MAX_SONGWRITING_SECTION_CHARS = 160

_ENTRY_MARKER = "\n\n[N0TE-SONGWRITE/1] "
_KEY_PART = re.compile(r"[^a-z0-9]+")


class SongwritingCaseHistoryError(RuntimeError):
    """A Song-bound writing case-history operation cannot proceed truthfully."""


class SongwritingCaseHistoryIntegrityError(SongwritingCaseHistoryError):
    """N0TE-owned writing scratch no longer matches its canonical encoding."""


@dataclass(frozen=True)
class SongwritingEntry:
    item_id: str
    sequence: int
    song_id: str
    session_id: str
    version_id: str | None
    session_state: str
    session_objective: str
    aspect: str
    kind: str
    section: str | None
    text: str
    promoted_claim_id: str | None
    source_kind: str = SONGWRITING_SOURCE_KIND
    provider_used: bool = False
    host_mutated: bool = False
    action_authority_granted: bool = False

    @property
    def promoted(self) -> bool:
        return self.promoted_claim_id is not None


@dataclass(frozen=True)
class SongwritingPromotion:
    entry: SongwritingEntry
    claim: EvidenceClaim
    semantic_key: str


class SongwritingCaseHistoryService:
    """Durable Songwriting/Vocal case history built on canonical Session memory.

    The service deliberately does not create a second lyrics database. Drafts,
    observations, unresolved questions, rejected ideas and decisions remain
    Session scratch until the artist explicitly promotes a DECISION. The
    Session already owns exact Artist/Song/Version binding, append-only history
    and promotion provenance, so this layer adds writing semantics without
    weakening those boundaries.

    This is case history, not pitch correction, transcription, comp execution,
    voice cloning, provider generation, or a claim that N0TE heard audio.
    """

    def __init__(self, store: LineageStore, sessions: SessionMemory):
        if not isinstance(store, LineageStore):
            raise TypeError("SongwritingCaseHistoryService requires LineageStore")
        if not isinstance(sessions, SessionMemory) or sessions.store is not store:
            raise TypeError(
                "SongwritingCaseHistoryService requires SessionMemory for the same LineageStore"
            )
        self.store = store
        self.sessions = sessions

    @staticmethod
    def normalize_aspect(value: str) -> str:
        aspect = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        if aspect not in SONGWRITING_ASPECTS:
            raise ValidationError(f"unsupported songwriting aspect: {aspect}")
        return aspect

    @staticmethod
    def normalize_kind(value: str) -> str:
        kind = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        if kind not in SONGWRITING_ENTRY_KINDS:
            raise ValidationError(f"unsupported songwriting entry kind: {kind}")
        return kind

    @staticmethod
    def _clean_text(value: str) -> str:
        if not isinstance(value, str):
            raise ValidationError("songwriting text must be text")
        text = value.strip()
        if not text:
            raise ValidationError("songwriting text must not be empty")
        if len(text) > MAX_SONGWRITING_TEXT_CHARS:
            raise ValidationError("songwriting text exceeds the local case-history limit")
        return text

    @staticmethod
    def _clean_section(value: str | None) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValidationError("songwriting section must be text")
        section = " ".join(value.split()).strip()
        if not section:
            return None
        if len(section) > MAX_SONGWRITING_SECTION_CHARS:
            raise ValidationError("songwriting section label is too long")
        return section

    @classmethod
    def _encode(cls, *, aspect: str, section: str | None, text: str) -> str:
        metadata = json.dumps(
            {"aspect": aspect, "section": section},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return text + _ENTRY_MARKER + metadata

    @classmethod
    def _decode(cls, body: str) -> tuple[str, str | None, str] | None:
        if _ENTRY_MARKER not in body:
            return None
        text, marker, metadata = body.rpartition(_ENTRY_MARKER)
        if not marker:
            return None
        try:
            payload = json.loads(metadata)
        except (TypeError, json.JSONDecodeError) as exc:
            raise SongwritingCaseHistoryIntegrityError(
                "Songwriting case-history metadata is unreadable"
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {"aspect", "section"}:
            raise SongwritingCaseHistoryIntegrityError(
                "Songwriting case-history metadata has an invalid shape"
            )
        try:
            aspect = cls.normalize_aspect(payload["aspect"])
            section = cls._clean_section(payload["section"])
            clean_text = cls._clean_text(text)
        except ValidationError as exc:
            raise SongwritingCaseHistoryIntegrityError(
                "Songwriting case-history metadata is not canonical"
            ) from exc
        if aspect != payload["aspect"] or section != payload["section"] or clean_text != text:
            raise SongwritingCaseHistoryIntegrityError(
                "Songwriting case-history encoding changed after capture"
            )
        return aspect, section, clean_text

    def _entry(self, item: SessionItem, session: SongSession) -> SongwritingEntry | None:
        if item.session_id != session.id:
            raise SongwritingCaseHistoryIntegrityError(
                "Songwriting item crossed its Session binding"
            )
        decoded = self._decode(item.body)
        if decoded is None:
            return None
        aspect, section, text = decoded
        promotion = self.sessions.promotion_for_item(item.id)
        return SongwritingEntry(
            item_id=item.id,
            sequence=item.sequence,
            song_id=session.song_id,
            session_id=session.id,
            version_id=session.version_id,
            session_state=session.state,
            session_objective=session.objective,
            aspect=aspect,
            kind=item.kind,
            section=section,
            text=text,
            promoted_claim_id=None if promotion is None else promotion.claim_id,
        )

    def capture(
        self,
        *,
        song_id: str,
        session_id: str,
        aspect: str,
        text: str,
        section: str | None = None,
        kind: str = "MARK",
    ) -> SongwritingEntry:
        song = self.store.get_song(str(song_id))
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_id}"
            )
        session = self.sessions.get_session(str(session_id))
        if session is None:
            raise NotFoundError(
                f"Session not found in profile {self.store.profile_id}: {session_id}"
            )
        if session.song_id != song.id:
            raise ValidationError("songwriting Session belongs to a different Song")
        if session.state != "OPEN":
            raise ValidationError("songwriting case history can be added only to an open Session")

        aspect = self.normalize_aspect(aspect)
        kind = self.normalize_kind(kind)
        section = self._clean_section(section)
        text = self._clean_text(text)
        item = self.sessions.append_scratch(
            session.id,
            kind=kind,
            body=self._encode(aspect=aspect, section=section, text=text),
        )
        entry = self._entry(item, session)
        if entry is None:
            raise SongwritingCaseHistoryIntegrityError(
                "captured songwriting item could not be read back"
            )
        return entry

    def entries_for_session(self, session_id: str) -> tuple[SongwritingEntry, ...]:
        session = self.sessions.get_session(str(session_id))
        if session is None:
            raise NotFoundError(
                f"Session not found in profile {self.store.profile_id}: {session_id}"
            )
        entries: list[SongwritingEntry] = []
        for item in self.sessions.items_for_session(session.id):
            entry = self._entry(item, session)
            if entry is not None:
                entries.append(entry)
        return tuple(entries)

    def entries_for_song(self, song_id: str) -> tuple[SongwritingEntry, ...]:
        song = self.store.get_song(str(song_id))
        if song is None:
            raise NotFoundError(
                f"Song not found in profile {self.store.profile_id}: {song_id}"
            )
        rows = self.store._conn.execute(
            "SELECT id FROM sessions WHERE song_id=? ORDER BY seq",
            (song.id,),
        ).fetchall()
        entries: list[SongwritingEntry] = []
        for row in rows:
            entries.extend(self.entries_for_session(str(row["id"])))
        return tuple(sorted(entries, key=lambda entry: entry.sequence))

    def entry(self, item_id: str) -> SongwritingEntry:
        row = self.store._conn.execute(
            "SELECT session_id FROM session_items WHERE id=?",
            (str(item_id),),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"Session item not found in profile {self.store.profile_id}: {item_id}"
            )
        entries = self.entries_for_session(str(row["session_id"]))
        for entry in entries:
            if entry.item_id == str(item_id):
                return entry
        raise ValidationError("Session item is not N0TE songwriting case history")

    @staticmethod
    def semantic_key(entry: SongwritingEntry) -> str:
        if not isinstance(entry, SongwritingEntry):
            raise TypeError("entry must be SongwritingEntry")
        section = "general" if entry.section is None else entry.section.lower()
        section = _KEY_PART.sub("_", section).strip("_") or "section"
        return f"songwriting.{entry.aspect.lower()}.{section[:64]}"

    def promote_decision(
        self,
        item_id: str,
        *,
        scope_kind: str = "SONG",
    ) -> SongwritingPromotion:
        entry = self.entry(item_id)
        if entry.kind != "DECISION":
            raise ValidationError(
                "only an explicit songwriting DECISION can become durable evidence"
            )
        key = self.semantic_key(entry)
        claim = self.sessions.promote_item(
            entry.item_id,
            scope_kind=scope_kind,
            key=key,
            source_kind=SONGWRITING_SOURCE_KIND,
            twin_domain="CREATIVE",
            confidence=1.0,
        )
        promoted = self.entry(entry.item_id)
        if promoted.promoted_claim_id != claim.id:
            raise SongwritingCaseHistoryIntegrityError(
                "songwriting decision promotion lost its Session provenance"
            )
        return SongwritingPromotion(entry=promoted, claim=claim, semantic_key=key)
