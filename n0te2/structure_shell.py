from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler
from typing import Callable, Mapping

from .consumer_shell import ConsumerShell, ConsumerShellError
from .lineage import ValidationError
from .structure import StaleSongStructureError
from .structure_service import SongStructureService, StructureBinding, StructureSectionBinding


def _binding_value(binding: StructureBinding) -> str:
    return json.dumps(
        [binding.song_id, binding.expected_revision_id],
        separators=(",", ":"),
    )


def _section_binding_value(binding: StructureSectionBinding) -> str:
    return json.dumps(
        [binding.song_id, binding.expected_revision_id, binding.section_index],
        separators=(",", ":"),
    )


def _decode_binding(value: str) -> StructureBinding:
    try:
        decoded = json.loads(value)
        if (
            not isinstance(decoded, list)
            or len(decoded) != 2
            or not isinstance(decoded[0], str)
            or not decoded[0]
            or (decoded[1] is not None and (not isinstance(decoded[1], str) or not decoded[1]))
        ):
            raise ValueError
        return StructureBinding(decoded[0], decoded[1])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise StaleSongStructureError("That Song Structure action is no longer valid.") from exc


def _decode_section_binding(value: str) -> StructureSectionBinding:
    try:
        decoded = json.loads(value)
        if (
            not isinstance(decoded, list)
            or len(decoded) != 3
            or not isinstance(decoded[0], str)
            or not decoded[0]
            or not isinstance(decoded[1], str)
            or not decoded[1]
            or isinstance(decoded[2], bool)
            or not isinstance(decoded[2], int)
            or decoded[2] < 0
        ):
            raise ValueError
        return StructureSectionBinding(decoded[0], decoded[1], decoded[2])
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise StaleSongStructureError("That Song section action is no longer valid.") from exc


def _section_markup(shell: ConsumerShell, service: SongStructureService, section, index: int) -> str:
    binding = service.section_binding(index)
    edit_token = shell._new_action("structure-edit", _section_binding_value(binding))
    remove_token = shell._new_action("structure-remove", _section_binding_value(binding))
    note = "" if section.note is None else section.note
    visible_note = "" if section.note is None else f'<p>{html.escape(section.note)}</p>'
    return (
        '<li class="stack">'
        f'<p><strong>{html.escape(section.label)}</strong> · Bars {section.first_bar}–{section.last_bar}</p>'
        f'{visible_note}'
        '<form class="stack" method="post" action="/structure/edit" '
        f'aria-label="Edit {html.escape(section.label, quote=True)} section">'
        f'{shell._hidden(edit_token)}'
        '<div><label>Section name'
        f'<input name="label" type="text" maxlength="120" value="{html.escape(section.label, quote=True)}" required></label></div>'
        '<div class="row"><label>First bar'
        f'<input name="first_bar" type="number" min="1" max="999999" step="1" value="{section.first_bar}" required></label>'
        '<label>Last bar'
        f'<input name="last_bar" type="number" min="1" max="999999" step="1" value="{section.last_bar}" required></label></div>'
        '<div><label>Section note (optional)'
        f'<textarea name="note" maxlength="600" rows="2">{html.escape(note)}</textarea></label></div>'
        '<button type="submit">Save section revision</button>'
        '</form>'
        '<form method="post" action="/structure/remove" '
        f'aria-label="Remove {html.escape(section.label, quote=True)} from Structure map">'
        f'{shell._hidden(remove_token)}'
        '<button type="submit">Remove from map</button>'
        '<p class="muted">This removes only N0TE Structure metadata. It does not delete, move, cut, or change audio in any DAW.</p>'
        '</form>'
        '</li>'
    )


def _structure_card(shell: ConsumerShell) -> str:
    hq = shell.runtime.headquarters
    song = hq.store.active_song()
    if song is None:
        return ""
    service = SongStructureService(hq.structure)
    current = hq.structure.current(song.id)
    history = hq.structure.history(song.id)
    binding = StructureBinding(song.id, None if current is None else current.id)
    add_token = shell._new_action("structure-add", _binding_value(binding))

    if current is None or not current.sections:
        sections = '<p class="muted">No Song sections are mapped yet.</p>'
    else:
        sections = (
            '<ol class="stack" aria-label="Portable Song sections">'
            + "".join(
                _section_markup(shell, service, section, index)
                for index, section in enumerate(current.sections)
            )
            + "</ol>"
        )

    revision = (
        '<p class="muted">No Structure revision yet.</p>'
        if current is None
        else f'<p class="muted">Structure revision {len(history)} · {len(current.sections)} section'
        f'{"" if len(current.sections) == 1 else "s"}</p>'
    )
    undo = ""
    if current is not None and current.parent_revision_id is not None:
        undo_token = shell._new_action("structure-undo", _binding_value(binding))
        undo = (
            '<form method="post" action="/structure/undo" aria-label="Undo last Song Structure change">'
            f'{shell._hidden(undo_token)}'
            '<button type="submit">Undo last Structure change</button>'
            '<p class="muted">Undo creates another revision from the prior map; it does not erase history or change DAW audio.</p>'
            '</form>'
        )

    add = (
        '<form class="stack" method="post" action="/structure/add" aria-label="Add Song section">'
        f'{shell._hidden(add_token)}'
        '<div><label>Section name<input name="label" type="text" maxlength="120" placeholder="Verse 1" required></label></div>'
        '<div class="row"><label>First bar<input name="first_bar" type="number" min="1" max="999999" step="1" required></label>'
        '<label>Last bar<input name="last_bar" type="number" min="1" max="999999" step="1" required></label></div>'
        '<div><label>Section note (optional)<textarea name="note" maxlength="600" rows="2"></textarea></label></div>'
        '<button type="submit">Add section to map</button>'
        '</form>'
    )
    return (
        '<div class="card"><h2>Song structure</h2>'
        '<p>This is N0TE’s portable arrangement map above individual DAWs. Bar ranges are artist-authored here; '
        'N0TE is not claiming they were derived from audio, and this map does not move or cut DAW material.</p>'
        f'{revision}{sections}{undo}<h3>Add a section</h3>{add}</div>'
    )


def _post_add(shell: ConsumerShell, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "structure-add")
    if action is None or action.value is None:
        shell._send_html(handler, 409, shell._simple_error("That Structure action was already handled or expired."))
        return
    service = SongStructureService(shell.runtime.headquarters.structure)
    service.add_section(
        _decode_binding(action.value),
        label=form.get("label", ""),
        first_bar=form.get("first_bar", ""),
        last_bar=form.get("last_bar", ""),
        note=form.get("note"),
    )
    shell._consumer_notice = "Song section added to the portable Structure map."
    shell._redirect(handler, "/song")


def _post_edit(shell: ConsumerShell, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "structure-edit")
    if action is None or action.value is None:
        shell._send_html(handler, 409, shell._simple_error("That section edit was already handled or expired."))
        return
    service = SongStructureService(shell.runtime.headquarters.structure)
    service.edit_section(
        _decode_section_binding(action.value),
        label=form.get("label", ""),
        first_bar=form.get("first_bar", ""),
        last_bar=form.get("last_bar", ""),
        note=form.get("note"),
    )
    shell._consumer_notice = "Song section revised. Earlier Structure history was preserved."
    shell._redirect(handler, "/song")


def _post_remove(shell: ConsumerShell, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "structure-remove")
    if action is None or action.value is None:
        shell._send_html(handler, 409, shell._simple_error("That section removal was already handled or expired."))
        return
    service = SongStructureService(shell.runtime.headquarters.structure)
    service.remove_section(_decode_section_binding(action.value))
    shell._consumer_notice = "Section removed from the N0TE Structure map. Audio and DAW material were not changed."
    shell._redirect(handler, "/song")


def _post_undo(shell: ConsumerShell, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
    action = shell._consume_action(form.get("action", ""), "structure-undo")
    if action is None or action.value is None:
        shell._send_html(handler, 409, shell._simple_error("That Structure undo was already handled or expired."))
        return
    service = SongStructureService(shell.runtime.headquarters.structure)
    service.undo_last(_decode_binding(action.value))
    shell._consumer_notice = "Prior Song Structure restored as a new revision."
    shell._redirect(handler, "/song")


def install_song_structure_map() -> None:
    """Attach the portable Song Structure card and four stale-safe actions once."""
    if getattr(ConsumerShell, "_song_structure_map_installed", False):
        return
    original_song: Callable[[ConsumerShell, object], str] = ConsumerShell._song_content
    original_post: Callable[[ConsumerShell, BaseHTTPRequestHandler], None] = ConsumerShell._handle_post

    def with_structure(self: ConsumerShell, state) -> str:
        rendered = original_song(self, state)
        marker = "</section>"
        if not rendered.endswith(marker):
            raise ConsumerShellError("Song page structure changed before Structure map could be attached safely")
        return rendered[: -len(marker)] + _structure_card(self) + marker

    def with_structure_posts(self: ConsumerShell, handler: BaseHTTPRequestHandler) -> None:
        path = self._path(handler)
        if path not in {"/structure/add", "/structure/edit", "/structure/remove", "/structure/undo"}:
            original_post(self, handler)
            return
        if not self._request_host_is_exact(handler) or not self._post_origin_is_allowed(handler):
            self._send_html(handler, 403, self._simple_error("That action did not come from this N0TE window."))
            return
        form = self._read_form(handler)
        if form is None or not self._form_authorized(form):
            self._send_html(handler, 403, self._simple_error("That action expired. Reload N0TE and try again."))
            return
        try:
            if path == "/structure/add":
                _post_add(self, handler, form)
            elif path == "/structure/edit":
                _post_edit(self, handler, form)
            elif path == "/structure/remove":
                _post_remove(self, handler, form)
            else:
                _post_undo(self, handler, form)
        except StaleSongStructureError as exc:
            self._send_html(handler, 409, self._simple_error(str(exc)))
        except ValidationError as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/song")
        except ConsumerShellError as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/song")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error("N0TE stopped that Structure action before it could become an unclear consumer state."),
            )

    ConsumerShell._song_content = with_structure
    ConsumerShell._handle_post = with_structure_posts
    ConsumerShell._song_structure_map_installed = True
