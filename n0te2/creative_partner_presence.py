from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json

from .lineage import ValidationError

PRESENCE_POLICY_VERSION = 1
PRESENCE_LEVELS = ("QUIET", "NUDGE", "COLLABORATE", "LEAD")
PRESENCE_SOURCES = ("SAFE_DEFAULT", "EXPLICIT_SESSION")
INTERRUPTION_COSTS = ("LOW", "NORMAL", "HIGH")
REQUIRED_ALERT_KINDS = (
    "SAFETY",
    "CONTRADICTION",
    "STALE_CONTEXT",
    "RIGHTS_PRIVACY",
)
PRESENCE_OUTCOMES = (
    "NO_ACTION",
    "RESPOND",
    "NUDGE",
    "COLLABORATE",
    "LEAD",
    "REQUIRED_ALERT",
)


class CreativePartnerPresenceError(RuntimeError):
    """A Presence decision cannot be prepared truthfully."""


class UnsupportedPresencePolicyError(CreativePartnerPresenceError):
    """The policy version is not understood by this runtime."""


class StalePresenceContextError(CreativePartnerPresenceError):
    """The policy was prepared for a different Artist/Song/Session/job context."""


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    normalized = value.strip()
    if not normalized:
        raise ValidationError(f"{label} must be non-empty")
    if len(normalized) > 512:
        raise ValidationError(f"{label} is too long")
    return normalized


def _optional_text(value: str | None, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _require_bool(value: bool, label: str) -> bool:
    if type(value) is not bool:
        raise ValidationError(f"{label} must be boolean")
    return value


@dataclass(frozen=True)
class PresenceContextBinding:
    """Opaque references to canonical context owners, not a second context store."""

    artist_id: str
    song_id: str | None = None
    session_id: str | None = None
    focus_id: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "artist_id", _required_text(self.artist_id, "artist_id"))
        object.__setattr__(self, "song_id", _optional_text(self.song_id, "song_id"))
        object.__setattr__(self, "session_id", _optional_text(self.session_id, "session_id"))
        object.__setattr__(self, "focus_id", _optional_text(self.focus_id, "focus_id"))
        object.__setattr__(self, "job_id", _optional_text(self.job_id, "job_id"))

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {
                "artist_id": self.artist_id,
                "song_id": self.song_id,
                "session_id": self.session_id,
                "focus_id": self.focus_id,
                "job_id": self.job_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PresencePolicy:
    """Ephemeral initiative policy for one exact current context.

    Presence changes whether N0TE speaks first and how much conversational structure
    it supplies. It never grants action authority or mutation permission.
    """

    binding: PresenceContextBinding
    level: str = "QUIET"
    source: str = "SAFE_DEFAULT"
    schema_version: int = PRESENCE_POLICY_VERSION
    lifecycle: str = "EPHEMERAL"

    def __post_init__(self) -> None:
        if not isinstance(self.binding, PresenceContextBinding):
            raise TypeError("binding must be PresenceContextBinding")
        if type(self.schema_version) is not int or self.schema_version != PRESENCE_POLICY_VERSION:
            raise UnsupportedPresencePolicyError(
                f"unsupported Creative Partner Presence policy version: {self.schema_version}"
            )
        level = normalize_presence_level(self.level)
        if not isinstance(self.source, str):
            raise ValidationError("Presence policy source must be text")
        source = self.source.strip().upper()
        if source not in PRESENCE_SOURCES:
            raise ValidationError(f"unsupported Presence policy source: {source}")
        if not isinstance(self.lifecycle, str):
            raise ValidationError("Presence policy lifecycle must be text")
        lifecycle = self.lifecycle.strip().upper()
        if lifecycle != "EPHEMERAL":
            raise ValidationError(
                "this Presence policy kernel is ephemeral; durable preference requires a separate explicit promotion path"
            )
        object.__setattr__(self, "level", level)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "lifecycle", lifecycle)

    @classmethod
    def safe_default(cls, binding: PresenceContextBinding) -> "PresencePolicy":
        """Old or policy-less contexts default to quiet, non-authorizing behavior."""
        return cls(binding=binding, level="QUIET", source="SAFE_DEFAULT")

    @classmethod
    def explicit_session(
        cls, binding: PresenceContextBinding, level: str
    ) -> "PresencePolicy":
        return cls(binding=binding, level=level, source="EXPLICIT_SESSION")


@dataclass(frozen=True)
class PresenceSignal:
    """Qualitative relevance evidence for one possible intervention.

    These booleans are explicit semantic reasons. They are deliberately not collapsed
    into a fabricated numeric relevance/confidence score.
    """

    semantic_key: str
    purpose_relevant: bool
    job_relevant: bool
    changes_next_decision: bool = False
    protects_context: bool = False
    prevents_meaningful_failure: bool = False
    explicitly_requested: bool = False
    actionable_now: bool = True
    interruption_cost: str = "NORMAL"
    deferred_not_now: bool = False
    required_alert_kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "semantic_key", _required_text(self.semantic_key, "semantic_key")
        )
        for field_name in (
            "purpose_relevant",
            "job_relevant",
            "changes_next_decision",
            "protects_context",
            "prevents_meaningful_failure",
            "explicitly_requested",
            "actionable_now",
            "deferred_not_now",
        ):
            _require_bool(getattr(self, field_name), field_name)
        if not isinstance(self.interruption_cost, str):
            raise ValidationError("interruption_cost must be text")
        cost = self.interruption_cost.strip().upper()
        if cost not in INTERRUPTION_COSTS:
            raise ValidationError(f"unsupported interruption cost: {cost}")
        object.__setattr__(self, "interruption_cost", cost)

        alert = self.required_alert_kind
        if alert is not None:
            if not isinstance(alert, str):
                raise ValidationError("required_alert_kind must be text")
            alert = alert.strip().upper().replace("-", "_").replace(" ", "_")
            if alert not in REQUIRED_ALERT_KINDS:
                raise ValidationError(f"unsupported required alert kind: {alert}")
        object.__setattr__(self, "required_alert_kind", alert)

    @property
    def material_relevance(self) -> bool:
        return bool(
            self.changes_next_decision
            or self.protects_context
            or self.prevents_meaningful_failure
        )


@dataclass(frozen=True)
class PresenceDecision:
    presence: str
    outcome: str
    should_interrupt: bool
    initiative: str
    reason_codes: tuple[str, ...]
    binding_fingerprint: str
    policy_version: int
    authority_effect: str = field(default="UNCHANGED", init=False)
    action_authority_granted: bool = field(default=False, init=False)
    mutation_authorized: bool = field(default=False, init=False)
    external_action_authorized: bool = field(default=False, init=False)

    @property
    def leave_it_alone(self) -> bool:
        return self.outcome == "NO_ACTION"


def normalize_presence_level(value: str) -> str:
    if not isinstance(value, str):
        raise ValidationError("Creative Partner Presence level must be text")
    level = value.strip().upper().replace("-", "_").replace(" ", "_")
    if level not in PRESENCE_LEVELS:
        raise ValidationError(f"unsupported Creative Partner Presence level: {level}")
    return level


class CreativePartnerPresenceService:
    """Resolve interruption/initiative without becoming an authority system."""

    @staticmethod
    def _decision(
        policy: PresencePolicy,
        outcome: str,
        *,
        should_interrupt: bool,
        initiative: str,
        reasons: tuple[str, ...],
    ) -> PresenceDecision:
        if outcome not in PRESENCE_OUTCOMES:
            raise CreativePartnerPresenceError(
                "Presence resolver produced an unsupported outcome"
            )
        return PresenceDecision(
            presence=policy.level,
            outcome=outcome,
            should_interrupt=should_interrupt,
            initiative=initiative,
            reason_codes=reasons,
            binding_fingerprint=policy.binding.fingerprint,
            policy_version=policy.schema_version,
        )

    def decide(
        self,
        policy: PresencePolicy,
        signal: PresenceSignal,
        *,
        current_binding: PresenceContextBinding,
    ) -> PresenceDecision:
        if not isinstance(policy, PresencePolicy):
            raise TypeError("policy must be PresencePolicy")
        if not isinstance(signal, PresenceSignal):
            raise TypeError("signal must be PresenceSignal")
        if not isinstance(current_binding, PresenceContextBinding):
            raise TypeError("current_binding must be PresenceContextBinding")
        if policy.binding != current_binding:
            raise StalePresenceContextError(
                "Creative Partner Presence context changed; rebind before deciding whether to interrupt."
            )

        # Required trust-boundary alerts cannot be hidden by Presence or Not Now.
        if signal.required_alert_kind is not None:
            return self._decision(
                policy,
                "REQUIRED_ALERT",
                should_interrupt=True,
                initiative="REQUIRED",
                reasons=("REQUIRED_ALERT", signal.required_alert_kind),
            )

        # Current explicit intent outranks an older unsolicited-suggestion deferral.
        if signal.explicitly_requested:
            return self._decision(
                policy,
                "RESPOND",
                should_interrupt=True,
                initiative="REACTIVE",
                reasons=("EXPLICIT_REQUEST",),
            )

        # Not Now remains authoritative for unsolicited interventions.
        if signal.deferred_not_now:
            return self._decision(
                policy,
                "NO_ACTION",
                should_interrupt=False,
                initiative="NONE",
                reasons=("DEFERRED_NOT_NOW",),
            )

        if not signal.material_relevance:
            return self._decision(
                policy,
                "NO_ACTION",
                should_interrupt=False,
                initiative="NONE",
                reasons=("NO_MATERIAL_RELEVANCE",),
            )

        # Mere musical relatedness is insufficient. An intervention outside both the
        # current purpose and job must protect context or prevent a meaningful failure
        # before it may compete for attention.
        if not signal.purpose_relevant and not signal.job_relevant:
            if not (signal.protects_context or signal.prevents_meaningful_failure):
                return self._decision(
                    policy,
                    "NO_ACTION",
                    should_interrupt=False,
                    initiative="NONE",
                    reasons=("OUTSIDE_CURRENT_PURPOSE_AND_JOB",),
                )

        if not signal.actionable_now:
            return self._decision(
                policy,
                "NO_ACTION",
                should_interrupt=False,
                initiative="NONE",
                reasons=("NOT_ACTIONABLE_NOW",),
            )

        if policy.level == "QUIET":
            return self._decision(
                policy,
                "NO_ACTION",
                should_interrupt=False,
                initiative="NONE",
                reasons=("QUIET_SUPPRESSES_UNSOLICITED",),
            )

        if policy.level == "NUDGE":
            important_enough = (
                signal.changes_next_decision or signal.prevents_meaningful_failure
            )
            if not important_enough:
                return self._decision(
                    policy,
                    "NO_ACTION",
                    should_interrupt=False,
                    initiative="NONE",
                    reasons=("BELOW_NUDGE_THRESHOLD",),
                )
            if (
                signal.interruption_cost == "HIGH"
                and not signal.prevents_meaningful_failure
            ):
                return self._decision(
                    policy,
                    "NO_ACTION",
                    should_interrupt=False,
                    initiative="NONE",
                    reasons=("HIGH_INTERRUPTION_COST",),
                )
            return self._decision(
                policy,
                "NUDGE",
                should_interrupt=True,
                initiative="LIGHT",
                reasons=("MATERIAL_NUDGE",),
            )

        if policy.level == "COLLABORATE":
            if signal.interruption_cost == "HIGH" and not (
                signal.changes_next_decision
                or signal.prevents_meaningful_failure
            ):
                return self._decision(
                    policy,
                    "NO_ACTION",
                    should_interrupt=False,
                    initiative="NONE",
                    reasons=("HIGH_INTERRUPTION_COST",),
                )
            return self._decision(
                policy,
                "COLLABORATE",
                should_interrupt=True,
                initiative="PARTNER",
                reasons=("MATERIAL_COLLABORATION",),
            )

        # LEAD increases initiative only. The result object still carries hard-false
        # authority and mutation flags.
        return self._decision(
            policy,
            "LEAD",
            should_interrupt=True,
            initiative="STRUCTURE_NEXT_DECISION",
            reasons=("MATERIAL_LEAD",),
        )
