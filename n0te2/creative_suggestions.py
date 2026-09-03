from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from .attention_deferral import AttentionDeferralMemory
from .lineage import LineageStore, ValidationError
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
SUGGESTION_ITEM_PREFIX = "SUGGESTION:"

_DISTANCE_EXPLANATIONS = {
    "FAMILIAR": "A small move around the current Song context. This does not claim to know your personal taste.",
    "ADJACENT": "Change one unlocked creative dimension while leaving the others alone as much as practical.",
    "WILDCARD": "Try a larger deliberate contrast in one unlocked dimension. It is an experiment, not a diagnosis.",
}

# Stable semantic keys are intentionally independent of Song/profile identity.
# Durable Attention state binds those keys to Artist/Song/context horizons.
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


def suggestion_item_key(semantic_key: str) -> str:
    key = str(semantic_key).strip().lower()
    known = {entry[0] for entries in _CATALOG.values() for entry in entries}
    if key not in known:
        raise ValidationError(f"unknown creative suggestion semantic key: {key}")
    return SUGGESTION_ITEM_PREFIX + key


def suggestion_semantic_key(item_key: str) -> str | None:
    item = str(item_key).strip()
    if not item.startswith(SUGGESTION_ITEM_PREFIX):
        return None
    key = item[len(SUGGESTION_ITEM_PREFIX):]
    try:
        suggestion_item_key(key)
    except ValidationError:
        return None
    return key


def suggestion_title(semantic_key: str) -> str:
    key = str(semantic_key).strip().lower()
    for entries in _CATALOG.values():
        for candidate_key, title, _ in entries:
            if candidate_key == key:
                return title
    raise ValidationError(f"unknown creative suggestion semantic key: {key}")


def suggestion_context_anchor(session_id: str | None) -> str:
    return "session:none" if session_id is None else f"session:{str(session_id).strip()}"


class CreativeSuggestionService:
    """Deterministic local creative prompts for the active Song.

    Suggestions remain provider-free and mutation-free. When canonical Attention
    deferrals are supplied, the service excludes semantically deferred ideas before
    choosing a prompt. With no applicable deferral, selection is byte-for-byte the
    same deterministic path introduced by SONG-01-SUGGEST-01.
    """

    def __init__(
        self,
        store: LineageStore,
        sessions: SessionMemory,
        deferrals: AttentionDeferralMemory | None = None,
    ):
        if not isinstance(store, LineageStore):
            raise TypeError("CreativeSuggestionService requires LineageStore")
        if not isinstance(sessions, SessionMemory) or sessions.store is not store:
            raise TypeError("CreativeSuggestionService requires SessionMemory for the same LineageStore")
        if deferrals is not None and (
            not isinstance(deferrals, AttentionDeferralMemory) or deferrals.store is not store
        ):
            raise TypeError("CreativeSuggestionService deferrals must use the same LineageStore")
        self.store = store
        self.sessions = sessions
        self.deferrals = deferrals

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

    def _is_deferred(
        self,
        semantic_key: str,
        *,
        song_id: str,
        session_id: str | None,
        released_song_ids: Iterable[str],
    ) -> bool:
        if self.deferrals is None:
            return False
        return self.deferrals.applies(
            suggestion_item_key(semantic_key),
            song_id=song_id,
            anchor=suggestion_context_anchor(session_id),
            released_song_ids=released_song_ids,
        )

    def suggest(
        self,
        *,
        distance: str,
        locked_dimensions=(),
        variation: int = 0,
        released_song_ids: Iterable[str] = (),
    ) -> CreativeSuggestion:
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

        dimensions = [dimension for dimension in CREATIVE_DIMENSIONS if dimension not in locks]
        if not dimensions:
            raise CreativeSuggestionError("Every creative dimension is locked. Unlock at least one dimension to vary the Song.")

        material = "|".join(
            (song.id, session_id or "", objective or "", mode, ",".join(locks), str(variation))
        ).encode("utf-8")
        digest = hashlib.sha256(material).digest()
        first_dimension_index = int.from_bytes(digest[:4], "big") % len(dimensions)

        candidates = []
        for dimension_offset in range(len(dimensions)):
            dimension = dimensions[(first_dimension_index + dimension_offset) % len(dimensions)]
            entries = _CATALOG[dimension]
            first_entry_index = int.from_bytes(digest[4:8], "big") % len(entries)
            for entry_offset in range(len(entries)):
                semantic_key, title, prompts = entries[
                    (first_entry_index + entry_offset) % len(entries)
                ]
                candidates.append((dimension, semantic_key, title, prompts))

        selected = None
        for candidate in candidates:
            if not self._is_deferred(
                candidate[1],
                song_id=song.id,
                session_id=session_id,
                released_song_ids=released_song_ids,
            ):
                selected = candidate
                break
        if selected is None:
            raise CreativeSuggestionError(
                "Every available suggestion is deferred for this context. Restore one deferred idea, change the Song/session context, or continue without a suggestion."
            )

        dimension, semantic_key, title, prompts = selected
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
