from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from .lineage import LineageCorruptionError, LineageStore, ValidationError
from .session import SessionMemory

SUGGESTION_DISTANCES = ("FAMILIAR", "ADJACENT", "WILDCARD")
CREATIVE_DIMENSIONS = (
    "ARRANGEMENT",
    "RHYTHM",
    "HARMONY",
    "MELODY",
    "SOUND",
    "DYNAMICS",
)
SUGGESTION_SOURCE_KIND = "DETERMINISTIC_LOCAL"
SUGGESTION_DEFERRAL_SCHEMA_VERSION = 1

_DISTANCE_EXPLANATIONS = {
    "FAMILIAR": "A small move around the current Song context. This does not claim to know your personal taste.",
    "ADJACENT": "Change one unlocked creative dimension while leaving the others alone as much as practical.",
    "WILDCARD": "Try a larger deliberate contrast in one unlocked dimension. It is an experiment, not a diagnosis.",
}

# Stable semantic keys are intentionally independent of Song/profile identity so a
# later attention/suppression contract can refer to the idea pattern without
# persisting this read-only suggestion result itself.
_CATALOG = {
    "ARRANGEMENT": (("arrangement:contrast-window", "Create one contrast window", {
        "FAMILIAR": "Choose 4–8 bars before the strongest section and remove one supporting layer. Compare whether the arrival reads more clearly.",
        "ADJACENT": "Choose one section boundary and change the density on only one side of it. Keep the musical material itself mostly intact.",
        "WILDCARD": "Rebuild one short section around deliberate negative space, then compare it against the current version instead of replacing the Song outright.",
    }),),
    "RHYTHM": (("rhythm:single-groove-variable", "Change one groove variable", {
        "FAMILIAR": "Keep the pattern, but move or remove one recurring rhythmic event for one section. Listen for a clearer pocket before doing anything else.",
        "ADJACENT": "Keep the harmony and melody fixed while changing one rhythmic subdivision or accent pattern in a single section.",
        "WILDCARD": "Try one section at half-time, double-time, or with a deliberately sparse pulse, then compare the emotional effect before keeping it.",
    }),),
    "HARMONY": (("harmony:one-chord-pressure-test", "Pressure-test one chord moment", {
        "FAMILIAR": "Keep the progression, but change the voicing or inversion of one chord where the section feels most exposed.",
        "ADJACENT": "Keep the melody and rhythm fixed while replacing one chord with a nearby functional or color alternative, then compare only that moment.",
        "WILDCARD": "Try one deliberately outside-color chord at a section boundary and treat it as a reversible experiment, not a new harmonic rule.",
    }),),
    "MELODY": (("melody:motif-variation", "Vary one motif, not the whole topline", {
        "FAMILIAR": "Keep the motif shape and change only its ending on one repeat.",
        "ADJACENT": "Keep the rhythm recognizable but alter the interval direction of one motif repeat.",
        "WILDCARD": "Answer the existing motif with a contrasting short phrase in one section, then compare whether the contrast earns its space.",
    }),),
    "SOUND": (("sound:role-preserving-swap", "Swap a sound without changing its job", {
        "FAMILIAR": "Keep the part and role exactly the same, but audition one nearby timbral variation.",
        "ADJACENT": "Keep the notes and rhythm fixed while changing the sound family for one supporting layer.",
        "WILDCARD": "Replace one non-lead texture with a sharply contrasting source while preserving its musical role, then compare before committing.",
    }),),
    "DYNAMICS": (("dynamics:section-energy-curve", "Reshape one energy curve", {
        "FAMILIAR": "Change the level or density of one supporting element across a single section so the section has a clearer rise or fall.",
        "ADJACENT": "Keep notes and arrangement fixed while exaggerating one section’s dynamic contrast against its neighbor.",
        "WILDCARD": "Make one expected loud moment intentionally restrained, or one restrained moment unexpectedly large, then judge the contrast in context.",
    }),),
}


class CreativeSuggestionError(RuntimeError):
    """A bounded local creative suggestion could not be prepared truthfully."""


@dataclass(frozen=True)
class CreativeSuggestion:
    semantic_key: str
    song_id: str
    session_id: str | None
    distance: str
    dimension: str
    title: str
    prompt: str
    distance_explanation: str
    song_title: str
    session_objective: str | None
    source_kind: str = SUGGESTION_SOURCE_KIND
    personalized: bool = False
    provider_used: bool = False
    action_authority_granted: bool = False


class CreativeSuggestionService:
    """Pure deterministic creative prompts for the active Song.

    This service is deliberately smaller than an Artist World or recommendation
    engine. It reads current Song/Session context, respects explicit dimension
    locks, and returns one bounded prompt. It persists only explicit, session-
    scoped deferrals; it does not infer taste, call AI/providers, mutate a DAW,
    or grant action authority.
    """

    def __init__(self, store: LineageStore, sessions: SessionMemory):
        if not isinstance(store, LineageStore):
            raise TypeError("CreativeSuggestionService requires LineageStore")
        if not isinstance(sessions, SessionMemory) or sessions.store is not store:
            raise TypeError("CreativeSuggestionService requires SessionMemory for the same LineageStore")
        self.store = store
        self.sessions = sessions
        self._ensure_deferral_schema()

    def _ensure_deferral_schema(self) -> None:
        table = "creative_suggestion_deferrals"
        try:
            exists = self.store._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None
            version_row = self.store._conn.execute(
                "SELECT value FROM metadata WHERE key='creative_suggestion_deferral_schema_version'"
            ).fetchone()
            version = None if version_row is None else str(version_row["value"])
            if exists:
                self._validate_deferral_schema()
                if version is None:
                    # Admit the unversioned shape written by the immediately
                    # preceding bounded increment only after structural proof.
                    with self.store._tx():
                        self.store._conn.execute(
                            "INSERT INTO metadata(key,value) VALUES(?,?)",
                            ("creative_suggestion_deferral_schema_version", str(SUGGESTION_DEFERRAL_SCHEMA_VERSION)),
                        )
                elif version != str(SUGGESTION_DEFERRAL_SCHEMA_VERSION):
                    raise LineageCorruptionError("unsupported creative suggestion deferral schema version")
                return
            if version is not None:
                raise LineageCorruptionError("creative suggestion deferral schema metadata/table mismatch")
            with self.store._tx():
                self.store._conn.execute(
                    """CREATE TABLE IF NOT EXISTS creative_suggestion_deferrals (
                        song_id TEXT NOT NULL REFERENCES songs(id),
                        session_context TEXT NOT NULL,
                        semantic_key TEXT NOT NULL,
                        PRIMARY KEY(song_id, session_context, semantic_key)
                    )"""
                )
                self.store._conn.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?)",
                    ("creative_suggestion_deferral_schema_version", str(SUGGESTION_DEFERRAL_SCHEMA_VERSION)),
                )
            self._validate_deferral_schema()
        except LineageCorruptionError:
            raise
        except sqlite3.DatabaseError as exc:
            raise CreativeSuggestionError("Creative suggestion deferrals are unavailable.") from exc

    def _validate_deferral_schema(self) -> None:
        columns = tuple(
            (str(row["name"]), str(row["type"]), int(row["notnull"]), int(row["pk"]))
            for row in self.store._conn.execute("PRAGMA table_info(creative_suggestion_deferrals)")
        )
        expected = (
            ("song_id", "TEXT", 1, 1),
            ("session_context", "TEXT", 1, 2),
            ("semantic_key", "TEXT", 1, 3),
        )
        foreign_keys = tuple(self.store._conn.execute("PRAGMA foreign_key_list(creative_suggestion_deferrals)"))
        song_foreign_key = any(
            str(row["table"]) == "songs" and str(row["from"]) == "song_id" and str(row["to"]) == "id"
            for row in foreign_keys
        )
        if columns != expected or not song_foreign_key:
            raise LineageCorruptionError("creative suggestion deferral schema is malformed")

    @staticmethod
    def _session_context(session_id: str | None) -> str:
        return session_id or "NO_SESSION"

    def defer(self, suggestion: CreativeSuggestion) -> None:
        """Durably put one suggestion aside for its exact Song/work context."""
        if not isinstance(suggestion, CreativeSuggestion):
            raise TypeError("defer requires a CreativeSuggestion")
        song = self.store.active_song()
        latest = None if song is None else self.sessions.latest_for_song(song.id)
        latest_session_id = None if latest is None else latest.id
        if (
            song is None
            or suggestion.song_id != song.id
            or suggestion.session_id != latest_session_id
        ):
            raise CreativeSuggestionError(
                "The Song or work Session changed. The old suggestion was not deferred."
            )
        try:
            with self.store._tx():
                self.store._conn.execute(
                    "INSERT OR IGNORE INTO creative_suggestion_deferrals"
                    "(song_id,session_context,semantic_key) VALUES(?,?,?)",
                    (
                        suggestion.song_id,
                        self._session_context(suggestion.session_id),
                        suggestion.semantic_key,
                    ),
                )
        except sqlite3.DatabaseError as exc:
            raise CreativeSuggestionError("The suggestion could not be deferred safely.") from exc

    def _deferred_keys(self, song_id: str, session_id: str | None) -> set[str]:
        return {
            str(row["semantic_key"])
            for row in self.store._conn.execute(
                "SELECT semantic_key FROM creative_suggestion_deferrals "
                "WHERE song_id=? AND session_context=?",
                (song_id, self._session_context(session_id)),
            )
        }

    @staticmethod
    def normalize_distance(value: str) -> str:
        distance = str(value).strip().upper().replace("-", "_").replace(" ", "_")
        if distance not in SUGGESTION_DISTANCES:
            raise ValidationError(f"unsupported suggestion distance: {distance}")
        return distance

    @staticmethod
    def normalize_locks(values) -> tuple[str, ...]:
        if values is None:
            return ()
        normalized: set[str] = set()
        for value in values:
            dimension = str(value).strip().upper().replace("-", "_").replace(" ", "_")
            if dimension not in CREATIVE_DIMENSIONS:
                raise ValidationError(f"unsupported creative dimension lock: {dimension}")
            normalized.add(dimension)
        return tuple(sorted(normalized))

    def suggest(self, *, distance: str, locked_dimensions=(), variation: int = 0) -> CreativeSuggestion:
        mode = self.normalize_distance(distance)
        locks = self.normalize_locks(locked_dimensions)
        if not isinstance(variation, int) or variation < 0 or variation > 1000:
            raise ValidationError("suggestion variation must be an integer from 0 through 1000")

        song = self.store.active_song()
        if song is None:
            raise CreativeSuggestionError("Start or select a Song before asking for a creative suggestion.")
        latest = self.sessions.latest_for_song(song.id)
        objective = None if latest is None else latest.objective
        session_id = None if latest is None else latest.id

        unlocked = [dimension for dimension in CREATIVE_DIMENSIONS if dimension not in locks]
        if not unlocked:
            raise CreativeSuggestionError("Every creative dimension is locked. Unlock at least one dimension to vary the Song.")

        deferred = self._deferred_keys(song.id, session_id)
        available = [
            (dimension, entry)
            for dimension in unlocked
            for entry in _CATALOG[dimension]
            if entry[0] not in deferred
        ]
        if not available:
            raise CreativeSuggestionError(
                "Every available suggestion is set aside for this work Session. Start a new Session to revisit them."
            )

        material = "|".join((song.id, session_id or "", objective or "", mode, ",".join(locks), str(variation))).encode("utf-8")
        digest = hashlib.sha256(material).digest()
        dimension, entry = available[int.from_bytes(digest[:4], "big") % len(available)]
        semantic_key, title, prompts = entry

        return CreativeSuggestion(
            semantic_key=semantic_key,
            song_id=song.id,
            session_id=session_id,
            distance=mode,
            dimension=dimension,
            title=title,
            prompt=prompts[mode],
            distance_explanation=_DISTANCE_EXPLANATIONS[mode],
            song_title=song.title,
            session_objective=objective,
        )
