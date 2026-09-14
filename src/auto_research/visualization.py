"""A read-only research map, served locally or exported as one offline HTML file.

This module reads Store snapshots only. Opening the viewer never reconciles jobs,
settles usage, launches agents, or serves files from the research directory.
"""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import re
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
import webbrowser

from .io import atomic_text

if TYPE_CHECKING:
    from .store import Store


_PROJECT_FIELDS = ("goal", "budget", "created_at")
# Attempts also contain host paths, process identities, backend errors and arbitrary
# request fields. None are necessary for the research map. Progress is research
# content; preserve it without recursively deleting meaningful field names.
_ATTEMPT_FIELDS = (
    "id",
    "node_id",
    "role",
    "state",
    "estimate",
    "hold",
    "cost",
    "cost_kind",
    "created_at",
    "updated_at",
    "agenda_version",
    "notes_version",
    "progress",
    "collected",
    "manual_reconciliation",
)
_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)/result#([A-Za-z0-9][A-Za-z0-9_.-]*)\Z")
_MARKERS = re.compile(r"__ARI_GRAPH_DATA__|__ARI_LIVE__")


def _select(value: dict, fields: tuple[str, ...]) -> dict:
    return {field: deepcopy(value[field]) for field in fields if field in value}


def _graph_state(snapshot: dict) -> dict:
    """Project a raw or previously projected snapshot onto the public view shape."""
    state = {
        "project": _select(snapshot.get("project", {}), _PROJECT_FIELDS),
        "agenda": deepcopy(snapshot.get("agenda", {})),
        "notes": deepcopy(snapshot.get("notes", {})),
        "nodes": deepcopy(snapshot.get("nodes", [])),
        "attempts": [_select(attempt, _ATTEMPT_FIELDS) for attempt in snapshot.get("attempts", [])],
        "budget": deepcopy(snapshot.get("budget", {})),
    }
    ids = {node["id"] for node in state["nodes"]}
    edges = []
    seen = set()

    def append(source_ref: object, target: str, kind: str, **extra: object) -> None:
        match = _REF.fullmatch(source_ref) if isinstance(source_ref, str) else None
        if not match or match[1] not in ids:
            return
        edge = {"source": match[1], "target": target, "kind": kind, "ref": source_ref, **extra}
        identity = json.dumps(edge, sort_keys=True, ensure_ascii=False)
        if identity not in seen:
            seen.add(identity)
            edges.append(edge)

    for node in state["nodes"]:
        for item in node.get("inputs", []):
            append(item.get("ref"), node["id"], "input", use=item.get("use", ""))
        for finding in (node.get("result") or {}).get("findings", []):
            if finding.get("revises"):
                new_ref = finding.get("ref") or f"{node['id']}/result#{finding['id']}"
                append(finding["revises"], node["id"], "revision", finding_ref=new_ref)
    # A revision records the new finding's declared relationship to older work.
    # It does not invalidate the old finding or establish the truth of either.
    state["edges"] = edges
    return state


def graph_snapshot(store: Store) -> dict:
    """Read one consistent snapshot and derive directed input/revision edges."""
    return _graph_state(store.snapshot())


def _template() -> str:
    return (
        resources.files("auto_research")
        .joinpath("assets", "research-map.html")
        .read_text(encoding="utf-8")
    )


def render_html(state: dict, live: bool = False) -> str:
    """Render a standalone document, safely embedding even hostile research text."""
    template = _template()
    if any(template.count(marker) != 1 for marker in ("__ARI_GRAPH_DATA__", "__ARI_LIVE__")):
        raise ValueError("Research map template must contain each data placeholder exactly once")
    data = json.dumps(_graph_state(state), ensure_ascii=False, sort_keys=True, allow_nan=False)
    # The payload lives inside a script element. HTML recognizes </script> even
    # inside JSON strings, so escaping quotes alone cannot safely embed the data.
    for char, escaped in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        data = data.replace(char, escaped)
    replacements = {"__ARI_GRAPH_DATA__": data, "__ARI_LIVE__": "true" if live else "false"}
    # One pass: a marker occurring in the research text must remain research text.
    return _MARKERS.sub(lambda match: replacements[match[0]], template)


def export_graph(store: Store, directory: str | Path, state: dict | None = None) -> Path:
    """Write research-map.html only when its content changes; never mutate Store."""
    html = render_html(store.snapshot() if state is None else state)
    destination = Path(directory) / "research-map.html"
    try:
        if destination.read_text(encoding="utf-8") == html:
            return destination
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    atomic_text(destination, html)
    return destination


class _MapServer(ThreadingHTTPServer):
    daemon_threads = True


def make_server(store: Store, port: int = 0) -> ThreadingHTTPServer:
    """Bind a not-yet-running HTTP server to loopback; callers own its lifecycle."""
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("Viewer port must be an integer between 0 and 65535")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            # Request URLs are untrusted and can contain terminal escapes.
            pass

        def _reply(self, status: int, content: str, content_type: str) -> None:
            encoded = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                "connect-src 'self'; img-src data:; base-uri 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:
            # Binding loopback alone does not stop DNS rebinding: a hostile page
            # can later resolve its own host to 127.0.0.1. Only our printed URL
            # and its localhost spelling may read snapshots.
            hosts = self.headers.get_all("Host", [])
            allowed = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            if self.server.server_port == 80:
                allowed.update({"127.0.0.1", "localhost"})
            if len(hosts) != 1 or hosts[0].strip().lower() not in allowed:
                self._reply(403, "Local viewer host required\n", "text/plain; charset=utf-8")
                return
            try:
                parsed = urlsplit(self.path)
            except ValueError:
                self._reply(404, "Not found\n", "text/plain; charset=utf-8")
                return
            if parsed.scheme or parsed.netloc or parsed.path not in {"/", "/api/graph"}:
                self._reply(404, "Not found\n", "text/plain; charset=utf-8")
                return
            try:
                state = graph_snapshot(store)
                if parsed.path == "/":
                    body = render_html(state, live=True)
                    content_type = "text/html; charset=utf-8"
                else:
                    body = json.dumps(state, ensure_ascii=False, allow_nan=False)
                    content_type = "application/json; charset=utf-8"
            except Exception:
                # No exception strings, absolute paths, prompts or backend config
                # reach the browser when the snapshot/template is unavailable.
                self._reply(
                    503,
                    '{"error":"Research snapshot temporarily unavailable"}',
                    "application/json; charset=utf-8",
                )
                return
            self._reply(200, body, content_type)

        def _method_not_allowed(self) -> None:
            self._reply(405, "Read-only viewer\n", "text/plain; charset=utf-8")

        do_POST = do_PUT = do_PATCH = do_DELETE = _method_not_allowed

    return _MapServer(("127.0.0.1", port), Handler)


def serve_view(store: Store, port: int = 0, open_browser: bool = True) -> None:
    """Print a local URL, optionally open it, and serve until Ctrl-C."""
    server = make_server(store, port)
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(url, flush=True)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                print("Could not open the browser; use the URL above.", file=sys.stderr)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
