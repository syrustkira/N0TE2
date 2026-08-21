from __future__ import annotations

import html
import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Mapping
from urllib.parse import parse_qs, urlsplit

from .app_runtime import ApplicationRuntime
from .instance import ProcessIdentity, ProcessProbe
from .profiles import ApplicationProfile, ApplicationProfiles, ProfileResolution

_LOOPBACK_HOST = "127.0.0.1"
_MAX_FORM_BYTES = 4096
_MAX_ARTIST_NAME = 120
_MAX_SONG_TITLE = 200
_NAV_ROUTES = {"/": "Home", "/song": "Song", "/now": "Now", "/settings": "Settings"}


class ConsumerShellError(RuntimeError):
    """Invalid or unsafe UX-01A consumer-shell operation."""


class ConsumerShellRecoveryRequired(ConsumerShellError):
    """The shell cannot stop safely because canonical runtime recovery is required."""


@dataclass(frozen=True)
class ShellAddress:
    host: str
    port: int

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class _Action:
    kind: str
    value: str | None = None


@dataclass(frozen=True)
class _PageState:
    kind: str
    title: str
    eyebrow: str
    message: str
    artist_name: str | None = None
    song_title: str | None = None
    profiles: tuple[ApplicationProfile, ...] = ()


class _LoopbackHTTPServer(HTTPServer):
    allow_reuse_address = False


_CSS = r"""
:root {
  color-scheme: dark;
  --bg: #0b0d10;
  --panel: #13171c;
  --panel-2: #191f26;
  --text: #f3f5f7;
  --muted: #a9b2bd;
  --line: #2a323c;
  --accent: #d9ff63;
  --accent-ink: #11150a;
  --danger: #ffb4aa;
  --radius: 18px;
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
* { box-sizing: border-box; }
html { background: var(--bg); font-size: 16px; }
body { margin: 0; min-height: 100vh; color: var(--text); background: radial-gradient(circle at 12% 0%, #18202a 0, var(--bg) 38rem); }
a { color: inherit; }
.skip-link { position: absolute; left: 1rem; top: -4rem; z-index: 10; padding: .75rem 1rem; background: var(--accent); color: var(--accent-ink); border-radius: .75rem; }
.skip-link:focus { top: 1rem; }
.shell { width: min(1180px, calc(100% - 2rem)); margin: 0 auto; padding: 1.25rem 0 3rem; }
.topbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: .5rem 0 1.25rem; }
.brand { display: flex; gap: .7rem; align-items: center; font-weight: 760; letter-spacing: -.02em; text-decoration: none; }
.brand-mark { width: 2.1rem; height: 2.1rem; display: grid; place-items: center; border: 1px solid var(--line); border-radius: .7rem; background: var(--panel); }
.local-badge { display: inline-flex; align-items: center; gap: .45rem; color: var(--muted); font-size: .88rem; }
.local-badge::before { content: ""; width: .55rem; height: .55rem; border-radius: 999px; background: var(--accent); }
.layout { display: grid; grid-template-columns: 13rem minmax(0, 1fr); gap: 1.25rem; align-items: start; }
.nav { position: sticky; top: 1rem; display: grid; gap: .35rem; }
.nav a { min-height: 44px; display: flex; align-items: center; padding: .7rem .85rem; color: var(--muted); text-decoration: none; border: 1px solid transparent; border-radius: .8rem; }
.nav a:hover, .nav a[aria-current="page"] { color: var(--text); background: var(--panel); border-color: var(--line); }
main { min-width: 0; }
.hero { padding: clamp(1.35rem, 4vw, 3rem); border: 1px solid var(--line); border-radius: calc(var(--radius) + 4px); background: linear-gradient(150deg, var(--panel-2), var(--panel)); box-shadow: 0 24px 70px rgba(0,0,0,.22); }
.eyebrow { margin: 0 0 .55rem; color: var(--accent); font-size: .78rem; font-weight: 760; letter-spacing: .11em; text-transform: uppercase; }
h1 { margin: 0; max-width: 18ch; font-size: clamp(2rem, 6vw, 4.6rem); line-height: .98; letter-spacing: -.055em; }
.lede { max-width: 62ch; margin: 1rem 0 0; color: var(--muted); font-size: 1.05rem; line-height: 1.65; }
.grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; margin-top: 1rem; }
.card { min-width: 0; padding: 1.25rem; border: 1px solid var(--line); border-radius: var(--radius); background: rgba(19,23,28,.94); }
.card h2 { margin: 0 0 .45rem; font-size: 1.05rem; }
.card p { margin: .35rem 0; color: var(--muted); line-height: 1.55; }
.song-name { margin-top: .5rem !important; color: var(--text) !important; font-size: clamp(1.35rem, 4vw, 2rem); font-weight: 760; letter-spacing: -.035em; overflow-wrap: anywhere; }
.stack { display: grid; gap: .75rem; }
.row { display: flex; flex-wrap: wrap; gap: .65rem; align-items: center; }
label { display: block; margin-bottom: .4rem; font-weight: 650; }
input[type="text"] { width: 100%; min-height: 46px; padding: .72rem .8rem; color: var(--text); background: #0e1216; border: 1px solid var(--line); border-radius: .75rem; font: inherit; }
button, .button { min-height: 44px; display: inline-flex; align-items: center; justify-content: center; gap: .4rem; padding: .72rem 1rem; border: 1px solid var(--line); border-radius: .75rem; background: var(--panel-2); color: var(--text); font: inherit; font-weight: 720; text-decoration: none; cursor: pointer; }
button.primary, .button.primary { border-color: var(--accent); background: var(--accent); color: var(--accent-ink); }
button.danger { color: var(--danger); }
button:hover, .button:hover { filter: brightness(1.08); }
button:focus-visible, a:focus-visible, input:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
.status { display: inline-flex; align-items: center; gap: .55rem; color: var(--muted); }
.status::before { content: ""; width: .65rem; height: .65rem; border-radius: 999px; background: currentColor; }
.status.good { color: #c8f77d; }
.status.caution { color: #ffd38b; }
.notice { margin-top: 1rem; padding: 1rem; border-left: 3px solid var(--accent); background: #11161b; color: var(--muted); border-radius: .6rem; }
.muted { color: var(--muted); }
footer { padding: 2rem 0 0; color: var(--muted); font-size: .85rem; }
@media (max-width: 760px) {
  .shell { width: min(100% - 1rem, 1180px); }
  .layout { grid-template-columns: 1fr; }
  .nav { position: static; grid-template-columns: repeat(4, minmax(0, 1fr)); overflow-x: auto; }
  .nav a { justify-content: center; padding-inline: .45rem; }
  .grid { grid-template-columns: 1fr; }
  .hero { padding: 1.25rem; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .001ms !important; animation-duration: .001ms !important; animation-iteration-count: 1 !important; }
}
""".strip()


def _clean_human_text(value: str, field: str, *, maximum: int) -> str:
    text = " ".join(str(value).split())
    if not text:
        raise ConsumerShellError(f"{field} must not be empty")
    if len(text) > maximum:
        raise ConsumerShellError(f"{field} is too long")
    return text


class ConsumerShell:
    """Local-first UX-01A Artist Headquarters front door.

    Product semantics remain in ApplicationProfiles/ApplicationRuntime/Headquarters.
    This shell owns only bounded presentation, browser-session action authority and
    loopback transport. The HTTP server is intentionally single-threaded because
    canonical SQLite Headquarters ownership is thread-bound.
    """

    def __init__(
        self,
        *,
        data_root: str | Path,
        state_root: str | Path,
        process: ProcessIdentity,
        probe: ProcessProbe,
        port: int = 0,
    ) -> None:
        data = Path(data_root)
        state = Path(state_root)
        if not data.is_absolute() or not state.is_absolute():
            raise ConsumerShellError("data_root and state_root must be absolute")
        if not isinstance(process, ProcessIdentity):
            raise TypeError("process must be ProcessIdentity")
        if not callable(getattr(probe, "status", None)):
            raise TypeError("probe must implement status(process)")
        if isinstance(port, bool) or not isinstance(port, int) or not (0 <= port <= 65535):
            raise ConsumerShellError("port must be an integer from 0 to 65535")

        self.data_root = data
        self.state_root = state
        self.process = process
        self.probe = probe
        self.port = port
        self.profiles = ApplicationProfiles(data_root=data, state_root=state)
        self.runtime = ApplicationRuntime(data_root=data, state_root=state)

        self._csrf = secrets.token_urlsafe(32)
        self._actions: dict[str, _Action] = {}
        self._server: _LoopbackHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._stop_requested = threading.Event()
        self._stop_error: str | None = None
        self._address: ShellAddress | None = None
        self._consumer_notice: str | None = None
        self._blocked_state: _PageState | None = None

    @property
    def address(self) -> ShellAddress:
        if self._address is None:
            raise ConsumerShellError("consumer shell is not started")
        return self._address

    @property
    def url(self) -> str:
        return self.address.origin + "/"

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stopped.is_set()

    def start(self) -> ShellAddress:
        if self._thread is not None:
            raise ConsumerShellError("one ConsumerShell instance can be started only once")

        shell = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "N0TE"
            sys_version = ""

            def do_GET(self) -> None:  # noqa: N802
                shell._handle_get(self)

            def do_POST(self) -> None:  # noqa: N802
                shell._handle_post(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        try:
            server = _LoopbackHTTPServer((_LOOPBACK_HOST, self.port), Handler)
        except OSError as exc:
            raise ConsumerShellError(f"local consumer shell could not bind safely: {exc}") from exc
        server.timeout = 0.1
        host, bound_port = server.server_address[:2]
        if host != _LOOPBACK_HOST:
            server.server_close()
            raise ConsumerShellError("consumer shell must bind only to IPv4 loopback")
        self._server = server
        self._address = ShellAddress(_LOOPBACK_HOST, int(bound_port))
        self._thread = threading.Thread(
            target=self._serve,
            name="n0te-consumer-shell",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=2.0):
            raise ConsumerShellError("consumer shell did not enter its serve loop")
        return self.address

    def _serve(self) -> None:
        server = self._server
        if server is None:
            self._stop_error = "consumer shell server disappeared before start"
            self._stopped.set()
            return
        self._ready.set()
        try:
            while True:
                if self._stop_requested.is_set():
                    if self.runtime.state != "STOPPED":
                        result = self.runtime.quit()
                        if result.status not in {"STOPPED", "ALREADY_STOPPED"}:
                            self._stop_error = "N0TE could not release the active Artist workspace safely"
                            self._stop_requested.clear()
                        else:
                            break
                    else:
                        break
                server.handle_request()
        finally:
            server.server_close()
            self._stopped.set()

    def stop(self, *, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        if self._stopped.is_set():
            return
        self._stop_requested.set()
        if not self._stopped.wait(timeout=timeout):
            message = self._stop_error or "consumer shell could not stop safely"
            raise ConsumerShellRecoveryRequired(message)

    def wait_stopped(self, *, timeout: float = 2.0) -> bool:
        return self._stopped.wait(timeout=timeout)

    def _expected_host(self) -> str:
        return f"{self.address.host}:{self.address.port}"

    def _request_host_is_exact(self, handler: BaseHTTPRequestHandler) -> bool:
        return handler.headers.get("Host", "").strip() == self._expected_host()

    def _post_origin_is_allowed(self, handler: BaseHTTPRequestHandler) -> bool:
        origin = handler.headers.get("Origin")
        if origin is None:
            return True
        return origin.strip() == self.address.origin

    @staticmethod
    def _path(handler: BaseHTTPRequestHandler) -> str:
        parsed = urlsplit(handler.path)
        if parsed.query or parsed.fragment:
            return "__invalid__"
        return parsed.path

    def _security_headers(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        handler.send_header("Referrer-Policy", "no-referrer")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.send_header("X-Frame-Options", "DENY")
        handler.send_header("Cache-Control", "no-store")

    def _send_bytes(
        self,
        handler: BaseHTTPRequestHandler,
        status: int,
        payload: bytes,
        *,
        content_type: str,
    ) -> None:
        handler.send_response(status)
        self._security_headers(handler)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(payload)))
        handler.end_headers()
        handler.wfile.write(payload)

    def _send_html(self, handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
        self._send_bytes(
            handler,
            status,
            body.encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )

    def _redirect(self, handler: BaseHTTPRequestHandler, path: str) -> None:
        handler.send_response(303)
        self._security_headers(handler)
        handler.send_header("Location", path)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    def _new_action(self, kind: str, value: str | None = None) -> str:
        token = secrets.token_urlsafe(24)
        self._actions[token] = _Action(kind, value)
        return token

    def _consume_action(self, token: str, expected_kind: str) -> _Action | None:
        action = self._actions.pop(str(token), None)
        if action is None or action.kind != expected_kind:
            return None
        return action

    def _reset_actions(self) -> None:
        self._actions.clear()

    def _read_form(self, handler: BaseHTTPRequestHandler) -> Mapping[str, str] | None:
        content_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return None
        try:
            length = int(handler.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > _MAX_FORM_BYTES:
            return None
        raw = handler.rfile.read(length)
        try:
            parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True, strict_parsing=False)
        except UnicodeDecodeError:
            return None
        values: dict[str, str] = {}
        for key, items in parsed.items():
            if len(items) != 1:
                return None
            values[str(key)] = str(items[0])
        return values

    def _form_authorized(self, form: Mapping[str, str]) -> bool:
        return secrets.compare_digest(form.get("csrf", ""), self._csrf)

    def _handle_get(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._request_host_is_exact(handler):
            self._send_html(handler, 421, self._simple_error("This N0TE window is available only from its exact local address."))
            return
        path = self._path(handler)
        if path == "/assets/shell.css":
            self._send_bytes(handler, 200, _CSS.encode("utf-8"), content_type="text/css; charset=utf-8")
            return
        if path not in _NAV_ROUTES:
            self._send_html(handler, 404, self._simple_error("That N0TE page is not available."))
            return
        try:
            if self.runtime.state != "RUNNING":
                state = self._ensure_runtime()
                if state is not None:
                    self._send_html(handler, 200, self._render_state(state, path="/"))
                    return
            self._send_html(handler, 200, self._render_running(path))
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error(
                    "N0TE protected your local work, but this page could not be prepared. Try again from the Home page."
                ),
            )

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._request_host_is_exact(handler) or not self._post_origin_is_allowed(handler):
            self._send_html(handler, 403, self._simple_error("That action did not come from this N0TE window."))
            return
        path = self._path(handler)
        if path not in {"/profile/create", "/profile/select", "/song/start", "/quit"}:
            self._send_html(handler, 404, self._simple_error("That N0TE action is not available."))
            return
        form = self._read_form(handler)
        if form is None or not self._form_authorized(form):
            self._send_html(handler, 403, self._simple_error("That action expired. Reload N0TE and try again."))
            return

        try:
            if path == "/profile/create":
                self._post_create_profile(handler, form)
            elif path == "/profile/select":
                self._post_select_profile(handler, form)
            elif path == "/song/start":
                self._post_start_song(handler, form)
            else:
                self._post_quit(handler, form)
        except ConsumerShellError as exc:
            self._consumer_notice = str(exc)
            self._redirect(handler, "/")
        except Exception:
            self._send_html(
                handler,
                500,
                self._simple_error("N0TE stopped that action before it could become an unclear consumer state."),
            )

    def _ensure_runtime(self) -> _PageState | None:
        if self.runtime.state == "RECOVERY_REQUIRED":
            return _PageState(
                "recovery",
                "Your Artist workspace needs recovery",
                "Protected state",
                "N0TE will not guess, steal ownership, or overwrite local creative state.",
            )
        resolution = self.profiles.resolve()
        if resolution.state == "NEEDS_CREATION":
            return _PageState(
                "create-profile",
                "Welcome to your Headquarters",
                "Start with the artist",
                "Tell N0TE the artist name for this local workspace. DAWs, AI, and services can wait until your work actually needs them.",
            )
        if resolution.state == "NEEDS_SELECTION":
            return _PageState(
                "select-profile",
                "Who are you working as today?",
                "Choose an Artist",
                "Each Artist workspace stays separate. Choosing one never merges or copies another Artist's history.",
                profiles=resolution.profiles,
            )
        if resolution.state == "RECOVERY_REQUIRED":
            return _PageState(
                "recovery",
                "A local Artist workspace needs recovery",
                "Protected state",
                "N0TE found local profile state it cannot safely interpret. Nothing was merged, replaced, or silently repaired.",
            )
        if resolution.state == "BOOTSTRAP_BUSY":
            return _PageState(
                "blocked",
                "N0TE is already preparing an Artist workspace",
                "Another N0TE action is active",
                "Return to the other N0TE window, or try again after it finishes.",
            )
        if resolution.state in {"CREATED", "SELECTED_EXISTING"} and resolution.selected_profile_id:
            return self._launch_profile(resolution.selected_profile_id)
        raise ConsumerShellError("N0TE could not determine a safe Artist workspace")

    def _launch_profile(self, profile_id: str) -> _PageState | None:
        result = self.runtime.launch(profile_id=profile_id, process=self.process, probe=self.probe)
        if result.status in {"STARTED", "ALREADY_RUNNING"}:
            self._blocked_state = None
            return None
        if result.status == "REOPEN_EXISTING":
            return _PageState(
                "blocked",
                "N0TE is already open for this Artist",
                "Existing window",
                "Return to the N0TE window that already owns this Artist workspace instead of opening a duplicate.",
            )
        if result.status == "HELD_BY_OTHER":
            return _PageState(
                "blocked",
                "This Artist is already open",
                "Existing N0TE session",
                "Use the existing N0TE window. Your local Artist workspace was not changed.",
            )
        if result.status == "UNCERTAIN":
            return _PageState(
                "recovery",
                "N0TE cannot prove this Artist is safe to reopen yet",
                "Protected state",
                "N0TE kept the existing ownership record instead of guessing. Close any other N0TE session and try again.",
            )
        if result.status in {"RECOVERY_REQUIRED", "START_FAILED"}:
            return _PageState(
                "recovery",
                "This Artist workspace could not open safely",
                "Recovery needed",
                "Your local creative state was not replaced. Try again after the workspace is safe to reopen.",
            )
        raise ConsumerShellError("N0TE returned an unsupported Artist launch state")

    def _post_create_profile(self, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
        action = self._consume_action(form.get("action", ""), "profile-create")
        if action is None:
            self._send_html(handler, 409, self._simple_error("That create action was already handled or expired."))
            return
        artist_name = _clean_human_text(form.get("artist_name", ""), "Artist name", maximum=_MAX_ARTIST_NAME)
        resolution = self.profiles.resolve(artist_name=artist_name, process=self.process, probe=self.probe)
        if resolution.state not in {"CREATED", "SELECTED_EXISTING"} or not resolution.selected_profile_id:
            self._blocked_state = self._resolution_state(resolution)
            self._redirect(handler, "/")
            return
        blocked = self._launch_profile(resolution.selected_profile_id)
        self._blocked_state = blocked
        self._redirect(handler, "/")

    def _post_select_profile(self, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
        action = self._consume_action(form.get("action", ""), "profile-select")
        if action is None or action.value is None:
            self._send_html(handler, 409, self._simple_error("That Artist choice was already handled or expired."))
            return
        resolution = self.profiles.resolve(selected_profile_id=action.value)
        if resolution.state != "SELECTED_EXISTING" or not resolution.selected_profile_id:
            self._blocked_state = self._resolution_state(resolution)
            self._redirect(handler, "/")
            return
        blocked = self._launch_profile(resolution.selected_profile_id)
        self._blocked_state = blocked
        self._redirect(handler, "/")

    def _post_start_song(self, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
        if self.runtime.state != "RUNNING":
            self._send_html(handler, 409, self._simple_error("Open an Artist workspace before starting a Song."))
            return
        action = self._consume_action(form.get("action", ""), "song-start")
        if action is None:
            self._send_html(handler, 409, self._simple_error("That Song action was already handled or expired."))
            return
        title = _clean_human_text(form.get("song_title", ""), "Song title", maximum=_MAX_SONG_TITLE)
        song = self.runtime.headquarters.store.create_song(title)
        self._consumer_notice = f"{song.title} is now your active Song."
        self._redirect(handler, "/song")

    def _post_quit(self, handler: BaseHTTPRequestHandler, form: Mapping[str, str]) -> None:
        action = self._consume_action(form.get("action", ""), "quit")
        if action is None:
            self._send_html(handler, 409, self._simple_error("That Quit action was already handled or expired."))
            return
        if self.runtime.state == "STOPPED":
            result_status = "ALREADY_STOPPED"
        else:
            result_status = self.runtime.quit().status
        if result_status not in {"STOPPED", "ALREADY_STOPPED"}:
            self._send_html(
                handler,
                503,
                self._render_state(
                    _PageState(
                        "recovery",
                        "N0TE could not finish quitting safely",
                        "Recovery needed",
                        "Your Artist workspace remains protected. N0TE will keep this local window available instead of pretending it closed cleanly.",
                    ),
                    path="/settings",
                ),
            )
            return
        self._send_html(handler, 200, self._closed_page())
        self._stop_requested.set()

    def _resolution_state(self, resolution: ProfileResolution) -> _PageState:
        if resolution.state == "NEEDS_SELECTION":
            return _PageState(
                "select-profile",
                "Who are you working as today?",
                "Choose an Artist",
                "Each Artist workspace stays separate.",
                profiles=resolution.profiles,
            )
        if resolution.state == "BOOTSTRAP_BUSY":
            return _PageState(
                "blocked",
                "N0TE is already preparing an Artist workspace",
                "Another N0TE action is active",
                "Return to the other N0TE window, or try again after it finishes.",
            )
        return _PageState(
            "recovery",
            "A local Artist workspace needs recovery",
            "Protected state",
            "N0TE stopped before merging, replacing, or guessing about local Artist state.",
        )

    def _running_state(self, path: str) -> _PageState:
        store = self.runtime.headquarters.store
        artist = store.artist()
        song = store.active_song()
        if path == "/song":
            if song is None:
                return _PageState(
                    "running-no-song",
                    "Start with the Song",
                    "Creative Studio",
                    "Name the Song you want to work on. N0TE will make it the active Song and keep it selected across Headquarters.",
                    artist_name=artist.display_name,
                )
            return _PageState(
                "running-song",
                song.title,
                "Active Song",
                "This Song stays selected while you move around Headquarters. Production depth arrives through the Song journey, not a second UI-only project model.",
                artist_name=artist.display_name,
                song_title=song.title,
            )
        if path == "/now":
            return _PageState(
                "running-now",
                "What matters now",
                "Attention",
                "Creative work stays in front. N0TE will surface other work here only when it is useful and relevant instead of flooding your session with admin.",
                artist_name=artist.display_name,
                song_title=None if song is None else song.title,
            )
        if path == "/settings":
            return _PageState(
                "running-settings",
                "Your N0TE",
                "Settings",
                "This shell is local. DAWs, AI, and external services are set up only when a job needs them.",
                artist_name=artist.display_name,
                song_title=None if song is None else song.title,
            )
        if song is None:
            return _PageState(
                "running-no-song",
                "What are we making today?",
                "Artist Headquarters",
                "Start a Song now. N0TE can learn the rest of your setup when the work actually asks for it.",
                artist_name=artist.display_name,
            )
        return _PageState(
            "running-home",
            "Pick up where you left off",
            "Artist Headquarters",
            "Your active Song is ready. N0TE keeps the Artist and Song context intact while you move through Headquarters.",
            artist_name=artist.display_name,
            song_title=song.title,
        )

    def _render_running(self, path: str) -> str:
        self._blocked_state = None
        return self._render_state(self._running_state(path), path=path)

    def _render_state(self, state: _PageState, *, path: str) -> str:
        self._reset_actions()
        notice = self._consumer_notice
        self._consumer_notice = None
        nav = self._nav(path) if self.runtime.state == "RUNNING" else ""
        content = self._state_content(state)
        notice_html = "" if not notice else f'<div class="notice" role="status" aria-live="polite">{html.escape(notice)}</div>'
        artist_context = ""
        if state.artist_name:
            artist_context = f'<span class="local-badge">{html.escape(state.artist_name)} · Local</span>'
        else:
            artist_context = '<span class="local-badge">Local-first</span>'
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{html.escape(state.title)} · N0TE</title>
  <link rel="stylesheet" href="/assets/shell.css">
</head>
<body>
<a class="skip-link" href="#main">Skip to your work</a>
<div class="shell">
  <header class="topbar">
    <a class="brand" href="/" aria-label="N0TE Artist Headquarters home"><span class="brand-mark" aria-hidden="true">N0</span><span>N0TE</span></a>
    {artist_context}
  </header>
  <div class="layout">
    {nav}
    <main id="main" tabindex="-1">
      <section class="hero" aria-labelledby="page-title">
        <p class="eyebrow">{html.escape(state.eyebrow)}</p>
        <h1 id="page-title">{html.escape(state.title)}</h1>
        <p class="lede">{html.escape(state.message)}</p>
      </section>
      {notice_html}
      {content}
    </main>
  </div>
  <footer>N0TE keeps the Artist and Song above individual tools.</footer>
</div>
</body>
</html>"""

    def _nav(self, current: str) -> str:
        links = []
        for route, label in _NAV_ROUTES.items():
            current_attr = ' aria-current="page"' if route == current else ""
            links.append(f'<a href="{route}"{current_attr}>{html.escape(label)}</a>')
        return f'<nav class="nav" aria-label="Headquarters">{"".join(links)}</nav>'

    def _hidden(self, action_token: str) -> str:
        return (
            f'<input type="hidden" name="csrf" value="{html.escape(self._csrf, quote=True)}">'
            f'<input type="hidden" name="action" value="{html.escape(action_token, quote=True)}">'
        )

    def _start_song_form(self) -> str:
        token = self._new_action("song-start")
        return f"""<form class="stack" method="post" action="/song/start">
{self._hidden(token)}
<div><label for="song-title">Song title</label><input id="song-title" name="song_title" type="text" maxlength="{_MAX_SONG_TITLE}" autocomplete="off" required></div>
<div class="row"><button class="primary" type="submit">Start this Song</button></div>
</form>"""

    def _state_content(self, state: _PageState) -> str:
        if state.kind == "create-profile":
            token = self._new_action("profile-create")
            return f"""<section class="grid" aria-label="Get started"><div class="card"><h2>Your Artist workspace</h2><p>One local Artist World keeps your Songs and context separate from everyone else's.</p><form class="stack" method="post" action="/profile/create">{self._hidden(token)}<div><label for="artist-name">Artist name</label><input id="artist-name" name="artist_name" type="text" maxlength="{_MAX_ARTIST_NAME}" autocomplete="nickname" required></div><button class="primary" type="submit">Open my Headquarters</button></form></div><div class="card"><h2>Setup can wait</h2><p>You do not need to connect a DAW, AI provider, account, or service to begin.</p><p class="status good">Local-first by default</p></div></section>"""
        if state.kind == "select-profile":
            choices = []
            for profile in state.profiles:
                token = self._new_action("profile-select", profile.profile_id)
                choices.append(
                    f'<form method="post" action="/profile/select">{self._hidden(token)}<button class="primary" type="submit">Work as {html.escape(profile.artist_name)}</button></form>'
                )
            return f'<section class="grid" aria-label="Artist selection"><div class="card stack"><h2>Your local Artists</h2>{"".join(choices)}</div><div class="card"><h2>Separate on purpose</h2><p>N0TE never merges Artist histories just because they live on the same machine.</p></div></section>'
        if state.kind in {"recovery", "blocked"}:
            level = "caution"
            return f'<section class="grid"><div class="card"><h2>Nothing was overwritten</h2><p class="status {level}">Protected local state</p><p>Try Home again after the other N0TE session or recovery condition is resolved.</p><a class="button" href="/">Try again</a></div></section>'
        if state.kind == "running-settings":
            quit_token = self._new_action("quit")
            song_line = "No active Song yet" if not state.song_title else f'Active Song: {html.escape(state.song_title)}'
            return f"""<section class="grid"><div class="card"><h2>Session</h2><p>{song_line}</p><p class="status good">Local shell active</p></div><div class="card"><h2>Connections</h2><p>DAWs, AI and services stay out of the way until a specific job needs them.</p></div><div class="card"><h2>Quit N0TE</h2><p>Quit closes Headquarters and releases this Artist workspace. Closing only the browser tab does not count as quitting.</p><form method="post" action="/quit">{self._hidden(quit_token)}<button class="danger" type="submit">Quit N0TE</button></form></div></section>"""
        if state.kind in {"running-no-song"}:
            return f'<section class="grid"><div class="card"><h2>Start a Song</h2><p>Create the durable Song you want N0TE to keep in context.</p>{self._start_song_form()}</div><div class="card"><h2>Nothing else required</h2><p>Production tools and connections can be added when this Song actually needs them.</p><p class="status good">Ready locally</p></div></section>'
        if state.kind in {"running-home", "running-song"}:
            assert state.song_title is not None
            return f'<section class="grid"><div class="card"><h2>Your active Song</h2><p class="song-name">{html.escape(state.song_title)}</p><a class="button primary" href="/song">Resume Song</a></div><div class="card"><h2>Context stays with you</h2><p>Move through Headquarters and come back. This Artist and Song remain the active creative context.</p><p class="status good">Song context active</p></div></section>'
        if state.kind == "running-now":
            song = "No active Song yet" if not state.song_title else f'Keep making {html.escape(state.song_title)}'
            return f'<section class="grid"><div class="card"><h2>Creative priority</h2><p class="song-name">{song}</p>{"" if state.song_title else self._start_song_form()}</div><div class="card"><h2>Quiet by design</h2><p>Jobs, approvals and business work will live here later, but they do not crowd the creative session before they are useful.</p></div></section>'
        raise ConsumerShellError(f"unsupported page state: {state.kind}")

    def _closed_page(self) -> str:
        return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>N0TE closed</title><style>body{font-family:system-ui,sans-serif;background:#0b0d10;color:#f3f5f7;display:grid;place-items:center;min-height:100vh;margin:0}main{max-width:42rem;padding:2rem}p{color:#a9b2bd;line-height:1.6}</style></head><body><main><p>Artist Headquarters</p><h1>N0TE closed safely.</h1><p>Your local Artist and Song state is preserved. You can close this browser tab.</p></main></body></html>"""

    def _simple_error(self, message: str) -> str:
        safe = html.escape(message)
        return f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>N0TE</title></head><body><main><h1>N0TE</h1><p>{safe}</p><p><a href="/">Return Home</a></p></main></body></html>'
