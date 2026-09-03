from __future__ import annotations

import html
import re
import uuid
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from .consumer_shell import ConsumerShell, ConsumerShellError
from .lineage import NotFoundError, ValidationError
from .template_library import TemplateLibraryError
from .templates import TEMPLATE_FAMILIES, TemplateDefinition, TemplateRole, TemplateValidationError

_MAX_TEMPLATE_NAME = 120
_MAX_TEMPLATE_INTENT = 500
_MAX_ROLE_DESCRIPTION = 500
_MAX_CAPABILITY = 160
_CAPABILITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


def _clean(value: str, field: str, maximum: int) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise ConsumerShellError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ConsumerShellError(f"{field} is too long")
    return text


def _family(value: str) -> str:
    family = str(value).strip().upper()
    if family not in TEMPLATE_FAMILIES:
        raise TemplateValidationError(f"unsupported template family: {family}")
    return family


def _capability(value: str) -> str:
    capability = str(value).strip()
    if not _CAPABILITY.fullmatch(capability):
        raise ConsumerShellError(
            "Capability key must use letters, numbers, dot, underscore, colon or hyphen."
        )
    return capability


def _save_action(shell: ConsumerShell, song_id: str) -> str:
    return shell._new_action("song-template-save", song_id)


def _select_action(shell: ConsumerShell, song_id: str, template_id: str) -> str:
    return shell._new_action("song-template-select", song_id + "\n" + template_id)


def _definition_markup(template: TemplateDefinition, *, selected: bool) -> str:
    roles = "".join(
        '<li><strong>'
        + html.escape(role.description)
        + '</strong><br><span class="muted">Capability: '
        + html.escape(role.capability)
        + (" · required" if role.required else " · optional")
        + "</span></li>"
        for role in template.roles
    )
    status = '<p class="status good">Selected for this Song</p>' if selected else ""
    return (
        '<div class="stack">'
        f'<p><strong>{html.escape(template.name)}</strong> · {html.escape(template.family.title())}</p>'
        f'{status}'
        f'<p>{html.escape(template.intent)}</p>'
        f'<ul class="stack">{roles}</ul>'
        '</div>'
    )


def _template_card(shell: ConsumerShell) -> str:
    if shell.runtime.state != "RUNNING":
        raise ConsumerShellError("Templates require an open Artist workspace")
    hq = shell.runtime.headquarters
    song = hq.store.active_song()
    if song is None:
        return ""

    templates = hq.template_library.all()
    selection = hq.template_library.selected_for_song(song.id)
    selected_id = None if selection is None else selection.template_id

    family_options = "".join(
        f'<option value="{html.escape(family)}">{html.escape(family.replace("_", " ").title())}</option>'
        for family in sorted(TEMPLATE_FAMILIES)
    )

    saved_rows = []
    for template in templates:
        selected = template.template_id == selected_id
        choose = ""
        if not selected:
            choose = (
                '<form method="post" action="/template/select">'
                f'{shell._hidden(_select_action(shell, song.id, template.template_id))}'
                '<button type="submit">Use for this Song</button>'
                '</form>'
            )
        saved_rows.append(
            '<li class="stack">'
            + _definition_markup(template, selected=selected)
            + choose
            + '</li>'
        )

    saved = (
        '<p class="muted">No saved Templates yet.</p>'
        if not saved_rows
        else '<ul class="stack" aria-label="Saved Templates">' + "".join(saved_rows) + '</ul>'
    )

    return (
        '<div class="card"><h2>Templates</h2>'
        '<p>Save a reusable starting intent above any DAW or provider, then choose which saved Template belongs with this Song.</p>'
        f'{saved}'
        '<details><summary>Save a reusable Template</summary>'
        '<form class="stack" method="post" action="/template/save" aria-label="Save a provider-neutral Template">'
        f'{shell._hidden(_save_action(shell, song.id))}'
        '<div><label>Name<input name="template_name" maxlength="120" required></label></div>'
        '<div><label>Family<select name="family" required>'
        f'{family_options}</select></label></div>'
        '<div><label>Intent<textarea name="intent" maxlength="500" required></textarea></label></div>'
        '<div><label>Capability key<input name="capability" maxlength="160" '
        'placeholder="vocal.tighten" required></label>'
        '<p class="muted">Use the stable N0TE capability this reusable start needs. This is semantic intent, not a plug-in, provider, DAW or track name.</p></div>'
        '<div><label>Role description<textarea name="role_description" maxlength="500" required></textarea></label></div>'
        '<label><input type="checkbox" name="optional" value="1"> This role is optional</label>'
        '<button class="primary" type="submit">Save and use for this Song</button>'
        '</form></details>'
        '<p class="muted"><strong>Application boundary:</strong> Selecting a Template only remembers a reusable start for this Song. N0TE has not changed a DAW, contacted a provider, or claimed this Template can be fully applied in the current studio. Real application readiness requires observed Studio capability facts and a later adapter execution contract.</p>'
        '</div>'
    )


def _post_save(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "song-template-save")
    if action is None or action.value is None:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That Template action was already handled or expired."),
        )
        return
    song = shell.runtime.headquarters.store.active_song()
    if song is None or song.id != action.value:
        shell._send_html(
            handler,
            409,
            shell._simple_error("The active Song changed. Reload the Song before saving a Template."),
        )
        return

    optional = form.get("optional")
    if optional not in {None, "1"}:
        raise ConsumerShellError("invalid optional-role value")
    template = TemplateDefinition(
        template_id="template:" + uuid.uuid4().hex,
        family=_family(form.get("family", "")),
        name=_clean(form.get("template_name", ""), "Template name", _MAX_TEMPLATE_NAME),
        intent=_clean(form.get("intent", ""), "Template intent", _MAX_TEMPLATE_INTENT),
        roles=(
            TemplateRole(
                role_id="primary",
                capability=_capability(form.get("capability", "")),
                description=_clean(
                    form.get("role_description", ""),
                    "Template role description",
                    _MAX_ROLE_DESCRIPTION,
                ),
                required=optional is None,
            ),
        ),
    )
    hq = shell.runtime.headquarters
    saved = hq.template_library.save(template)
    hq.template_library.select_for_song(song.id, saved.template_id)
    shell._consumer_notice = "Template saved and selected for this Song. Nothing was applied to a DAW."
    shell._redirect(handler, "/song")


def _post_select(
    shell: ConsumerShell,
    handler: BaseHTTPRequestHandler,
    form: Mapping[str, str],
) -> None:
    action = shell._consume_action(form.get("action", ""), "song-template-select")
    if action is None or action.value is None or "\n" not in action.value:
        shell._send_html(
            handler,
            409,
            shell._simple_error("That Template selection was already handled or expired."),
        )
        return
    song_id, template_id = action.value.split("\n", 1)
    song = shell.runtime.headquarters.store.active_song()
    if song is None or song.id != song_id:
        shell._send_html(
            handler,
            409,
            shell._simple_error("The active Song changed. Reload before selecting a Template."),
        )
        return
    shell.runtime.headquarters.template_library.select_for_song(song.id, template_id)
    shell._consumer_notice = "Template selected for this Song. Nothing was applied to a DAW."
    shell._redirect(handler, "/song")


def install_song_template_library() -> None:
    """Attach the provider-neutral Template library and protected actions once."""
    if getattr(ConsumerShell, "_song_template_library_installed", False):
        return

    original_song: Callable[[ConsumerShell, object], str] = ConsumerShell._song_content
    original_post: Callable[[ConsumerShell, BaseHTTPRequestHandler], None] = ConsumerShell._handle_post

    def with_template_card(self: ConsumerShell, state) -> str:
        rendered = original_song(self, state)
        card = _template_card(self)
        if not card:
            return rendered
        marker = "</section>"
        if not rendered.endswith(marker):
            raise ConsumerShellError(
                "Song page structure changed before Templates could attach safely"
            )
        return rendered[: -len(marker)] + card + marker

    def with_template_post(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        path = self._path(handler)
        if path not in {"/template/save", "/template/select"}:
            original_post(self, handler)
            return
        if not self._request_host_is_exact(handler) or not self._post_origin_is_allowed(handler):
            self._send_html(
                handler,
                403,
                self._simple_error("That action did not come from this N0TE window."),
            )
            return
        form = self._read_form(handler)
        if form is None or not self._form_authorized(form):
            self._send_html(
                handler,
                403,
                self._simple_error("That action expired. Reload N0TE and try again."),
            )
            return
        try:
            if path == "/template/save":
                _post_save(self, handler, form)
            else:
                _post_select(self, handler, form)
        except (
            ConsumerShellError,
            TemplateLibraryError,
            TemplateValidationError,
            ValidationError,
            NotFoundError,
        ) as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/song")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE stopped that Template action before it could become an unclear consumer state."
                ),
            )

    ConsumerShell._song_content = with_template_card
    ConsumerShell._handle_post = with_template_post
    ConsumerShell._song_template_library_installed = True
