from __future__ import annotations

from dataclasses import dataclass

from .learning import LearningEpisode, LearningMemory
from .lineage import ValidationError

INTERACTION_DEPTH_MODES = (
    "DO_IT",
    "WITH_ME",
    "SHOW_ME",
    "EXPLAIN_WHY",
    "LET_ME_TRY",
)

_MODE_LABELS = {
    "DO_IT": "DO IT",
    "WITH_ME": "WITH ME",
    "SHOW_ME": "SHOW ME",
    "EXPLAIN_WHY": "EXPLAIN WHY",
    "LET_ME_TRY": "LET ME TRY",
}


class InteractionDepthError(RuntimeError):
    """A requested collaboration-depth plan cannot be prepared truthfully."""


class StaleInteractionDepthError(InteractionDepthError):
    """The Song or Learning evidence moved after the interaction choice was prepared."""


@dataclass(frozen=True)
class InteractionDepthBinding:
    song_id: str
    episode_id: str
    expected_consequence_ids: tuple[str, ...]
    expected_decision_id: str | None


@dataclass(frozen=True)
class InteractionDepthPlan:
    mode: str
    label: str
    song_id: str
    episode_id: str
    subject: str
    change: str
    n0te_role: str
    artist_role: str
    next_step: str
    execution_requested: bool
    action_authority_granted: bool = False
    mutation_permitted_by_mode: bool = False


class InteractionDepthService:
    """Separate collaboration depth from mutation authority for one real job.

    A mode expresses how much agency the artist wants N0TE to take. It never grants
    action authority, execution eligibility, provider access, entitlement, approval,
    or mutation permission. The current bounded consumer seam is one canonical
    Learning episode so mode guidance stays attached to real Song work rather than
    becoming detached courseware or a global personality preference.
    """

    def __init__(self, learning: LearningMemory):
        if not isinstance(learning, LearningMemory):
            raise TypeError("InteractionDepthService requires canonical LearningMemory")
        self.learning = learning
        self.store = learning.store

    @staticmethod
    def normalize_mode(value: str) -> str:
        mode = str(value).strip().upper().replace(" ", "_")
        if mode not in INTERACTION_DEPTH_MODES:
            raise ValidationError(f"unsupported interaction depth: {mode}")
        return mode

    @staticmethod
    def _binding_for(episode: LearningEpisode) -> InteractionDepthBinding:
        return InteractionDepthBinding(
            song_id=episode.song_id,
            episode_id=episode.id,
            expected_consequence_ids=tuple(item.id for item in episode.consequences),
            expected_decision_id=None if episode.decision is None else episode.decision.id,
        )

    def binding_for(self, episode_id: str) -> InteractionDepthBinding:
        episode = self.learning.get_episode(str(episode_id))
        active = self.store.active_song()
        if episode is None or active is None or episode.song_id != active.id:
            raise StaleInteractionDepthError(
                "That Learning job is no longer part of the active Song."
            )
        return self._binding_for(episode)

    def current_binding(self) -> InteractionDepthBinding | None:
        song = self.store.active_song()
        if song is None:
            return None
        episodes = self.learning.episodes_for_song(song.id)
        for episode in reversed(episodes):
            if episode.decision is None:
                return self._binding_for(episode)
        return None

    def _episode_for_binding(self, binding: InteractionDepthBinding) -> LearningEpisode:
        if not isinstance(binding, InteractionDepthBinding):
            raise TypeError("binding must be InteractionDepthBinding")
        active = self.store.active_song()
        episode = self.learning.get_episode(binding.episode_id)
        if active is None or active.id != binding.song_id:
            raise StaleInteractionDepthError(
                "The active Song changed after this interaction choice was prepared."
            )
        if episode is None or episode.song_id != binding.song_id:
            raise StaleInteractionDepthError(
                "That Learning job no longer belongs to the active Song."
            )
        current_consequence_ids = tuple(item.id for item in episode.consequences)
        current_decision_id = None if episode.decision is None else episode.decision.id
        if (
            current_consequence_ids != binding.expected_consequence_ids
            or current_decision_id != binding.expected_decision_id
        ):
            raise StaleInteractionDepthError(
                "That Learning job changed after this interaction choice was prepared."
            )
        if episode.decision is not None:
            raise StaleInteractionDepthError(
                "That Learning job already has a final decision. Start or choose an open job."
            )
        return episode

    @staticmethod
    def _plan_for(episode: LearningEpisode, mode: str) -> InteractionDepthPlan:
        common = dict(
            mode=mode,
            label=_MODE_LABELS[mode],
            song_id=episode.song_id,
            episode_id=episode.id,
            subject=episode.subject_ref,
            change=episode.change_description,
            action_authority_granted=False,
            mutation_permitted_by_mode=False,
        )
        if mode == "DO_IT":
            return InteractionDepthPlan(
                **common,
                n0te_role=(
                    "Take the lead on this job only as far as an already verified capability, "
                    "separate action authority, and the exact execution surface permit."
                ),
                artist_role=(
                    "Judge the result and provide any approval that a consequential action "
                    "separately requires."
                ),
                next_step=(
                    "This Learning surface does not itself bind an executor. Treat DO IT as a "
                    "request for maximum N0TE agency, not a claim that the change was executed. "
                    "Use a verified execution surface when one exists, then record what actually happened."
                ),
                execution_requested=True,
            )
        if mode == "WITH_ME":
            return InteractionDepthPlan(
                **common,
                n0te_role=(
                    "Keep the Song context, explain one bounded step at a time, and help compare "
                    "what changes without taking the artist's judgment away."
                ),
                artist_role=(
                    "Perform or approve each meaningful step, listen or inspect the result, and "
                    "say what should happen next."
                ),
                next_step=(
                    f"Work through one bounded part of '{episode.change_description}' together, "
                    "then record the observed consequence before deciding."
                ),
                execution_requested=False,
            )
        if mode == "SHOW_ME":
            return InteractionDepthPlan(
                **common,
                n0te_role=(
                    "Demonstrate the approach as a read-only example or walkthrough tied to this "
                    "Song job; do not change the project merely to demonstrate it."
                ),
                artist_role="Watch the demonstration, question it, and decide whether to try it.",
                next_step=(
                    f"Show how to approach '{episode.subject_ref}' for the specific change "
                    f"'{episode.change_description}' without claiming a project mutation occurred."
                ),
                execution_requested=False,
            )
        if mode == "EXPLAIN_WHY":
            return InteractionDepthPlan(
                **common,
                n0te_role=(
                    "Explain the reasoning, tradeoffs, uncertainty, and evidence behind this job "
                    "without turning explanation into mutation."
                ),
                artist_role="Challenge the reasoning and keep final authority over musical taste.",
                next_step=(
                    f"Explain why '{episode.change_description}' could be worth testing, what would "
                    "count as useful evidence, and what alternative explanations could remain."
                ),
                execution_requested=False,
            )
        return InteractionDepthPlan(
            **common,
            n0te_role=(
                "Stand back while the artist acts, preserve the exact job context, and be ready to "
                "help observe, compare, or explain afterward."
            ),
            artist_role=(
                f"Try '{episode.change_description}' yourself, then describe or capture what actually changed."
            ),
            next_step=(
                "Let the artist make the next move first. N0TE should not mutate the project from "
                "this mode; afterward, record observed consequences and decide what was learned."
            ),
            execution_requested=False,
        )

    def plan(
        self,
        binding: InteractionDepthBinding,
        mode: str,
    ) -> InteractionDepthPlan:
        normalized = self.normalize_mode(mode)
        episode = self._episode_for_binding(binding)
        return self._plan_for(episode, normalized)
