"""Read-only snapshot projection, safe embedding and local HTTP boundaries."""

import copy
from contextlib import redirect_stderr, redirect_stdout
import http.client
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from auto_research.artifacts import ArtifactStore
from auto_research.store import Store
from auto_research import visualization as view


TEMPLATE = """<!doctype html><html><body>
<script type="application/json" id="ari-graph-data">__ARI_GRAPH_DATA__</script>
<script>const LIVE = __ARI_LIVE__;</script></body></html>"""


def embedded_state(document):
    payload = re.search(r'id="ari-graph-data">(.*?)</script>', document, re.DOTALL)
    assert payload is not None
    return json.loads(payload[1])


class VisualizationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root / "project")
        self.store.initialize(
            "Understand an unfinished argument",
            budget=100,
            config={"backend_command": ["PRIVATE_CONFIG"], "control": "running"},
        )
        self.template = patch.object(view, "_template", return_value=TEMPLATE)
        self.template.start()
        self.addCleanup(self.template.stop)

    def propose(self, **changes):
        return self.store.propose(
            {
                "question": "Q-001",
                "why_now": "A missing step remains",
                "plan": "Inspect the gap",
                **changes,
            }
        )

    def source_node(self):
        source = self.root / "source"
        source.mkdir(exist_ok=True)
        (source / "draft.txt").write_text("Assumption A remains unproved.")
        artifact = ArtifactStore(self.store.root).freeze(source / "draft.txt", source)
        node = self.propose(config={"research_example": "preserved research metadata"})
        attempt = self.store.reserve(node["id"], estimate=2)
        closed = self.store.publish(
            node["id"],
            {
                "close_reason": "Saved the conditional argument",
                "limitations": "A is unproved",
                "products": [{"id": "draft", "status": "partial", "gaps": ["A"], **artifact}],
                "findings": [
                    {
                        "id": "old",
                        "text": "If A, then B",
                        "conditions": "Assuming A",
                        "evidence": ["#draft"],
                    }
                ],
                "next": ["Check A"],
            },
        )
        self.store.settle(attempt["id"], 2)
        return closed, attempt

    def start_server(self, store=None):
        server = view.make_server(self.store if store is None else store)
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        thread.start()

        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(2)

        self.addCleanup(cleanup)
        return server

    @staticmethod
    def get(server, path="/api/graph", method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def test_snapshot_keeps_research_and_removes_process_fields_without_mutation(self):
        node, attempt = self.source_node()
        self.store.set_attempt(
            attempt["id"],
            workspace="PRIVATE_WORKSPACE",
            job_dir="PRIVATE_JOB",
            request={"secret": "PRIVATE_REQUEST"},
            prompt="PRIVATE_PROMPT",
            config="PRIVATE_ATTEMPT_CONFIG",
            error="PRIVATE_BACKEND_ERROR",
            identity="PRIVATE_PROCESS",
            session_id="PRIVATE_SESSION",
            progress={"state": "Waiting for A", "config": {"research_field": "preserve"}},
        )
        raw = self.store.snapshot()
        unchanged = copy.deepcopy(raw)
        with patch.object(self.store, "snapshot", return_value=raw) as snapshot:
            state = view.graph_snapshot(self.store)
        snapshot.assert_called_once_with()
        self.assertEqual(raw, unchanged)
        self.assertEqual(state["nodes"][0], raw["nodes"][0])
        self.assertEqual(state["nodes"][0]["config"], node["config"])
        self.assertEqual(state["attempts"][0]["progress"], raw["attempts"][0]["progress"])
        self.assertNotIn("config", state["project"])
        encoded = json.dumps(state)
        for marker in (
            "PRIVATE_CONFIG",
            "PRIVATE_WORKSPACE",
            "PRIVATE_JOB",
            "PRIVATE_REQUEST",
            "PRIVATE_PROMPT",
            "PRIVATE_ATTEMPT_CONFIG",
            "PRIVATE_BACKEND_ERROR",
            "PRIVATE_PROCESS",
            "PRIVATE_SESSION",
        ):
            self.assertNotIn(marker, encoded)
        state["nodes"][0]["result"]["limitations"] = "changed view only"
        self.assertEqual(raw, unchanged)

    def test_input_and_revision_edges_keep_refs_and_do_not_invalidate(self):
        source, _ = self.source_node()
        old = f"{source['id']}/result#old"
        draft = f"{source['id']}/result#draft"
        new = self.propose(inputs=[{"ref": draft, "use": "Extend its argument"}])
        attempt = self.store.reserve(new["id"])
        self.store.publish(
            new["id"],
            {
                "close_reason": "Recorded a narrower claim",
                "limitations": "A still open",
                "findings": [
                    {
                        "id": "new",
                        "text": "A revised conditional claim",
                        "conditions": "Assuming A and C",
                        "evidence": [draft],
                        "revises": old,
                    }
                ],
            },
        )
        self.store.settle(attempt["id"], 0)
        state = view.graph_snapshot(self.store)
        self.assertEqual(
            state["edges"],
            [
                {
                    "source": source["id"],
                    "target": new["id"],
                    "kind": "input",
                    "ref": draft,
                    "use": "Extend its argument",
                },
                {
                    "source": source["id"],
                    "target": new["id"],
                    "kind": "revision",
                    "ref": old,
                    "finding_ref": f"{new['id']}/result#new",
                },
            ],
        )
        self.assertNotIn("invalidated", json.dumps(state))
        self.assertEqual(state["nodes"][0]["result"]["findings"][0]["text"], "If A, then B")

    def test_embed_blocks_script_breakout_and_preserves_marker_text(self):
        malicious = '</script><script>alert("injected")</script>&\u2028\u2029 __ARI_LIVE__ __ARI_GRAPH_DATA__'
        raw = self.store.snapshot()
        raw["project"]["goal"] = malicious
        raw["request"] = "PRIVATE_REQUEST"
        raw["attempts"] = [{"id": "A-test", "state": "running", "prompt": "PRIVATE_PROMPT"}]
        html = view.render_html(raw)
        self.assertEqual(embedded_state(html)["project"]["goal"], malicious)
        self.assertNotIn('<script>alert("injected")', html)
        self.assertNotIn("PRIVATE_", html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertIn("const LIVE = false;", html)
        self.assertIn("const LIVE = true;", view.render_html(raw, live=True))

    def test_render_uses_packaged_resource_and_rejects_broken_template(self):
        self.template.stop()
        package = self.root / "package"
        (package / "assets").mkdir(parents=True)
        (package / "assets" / "research-map.html").write_text(TEMPLATE)
        with patch.object(view.resources, "files", return_value=package) as files:
            html = view.render_html(self.store.snapshot())
        files.assert_called_once_with("auto_research")
        self.assertEqual(embedded_state(html)["project"]["goal"], self.store.project()["goal"])
        with patch.object(view, "_template", return_value="__ARI_GRAPH_DATA__"):
            with self.assertRaisesRegex(ValueError, "placeholder"):
                view.render_html(self.store.snapshot())

    def test_export_reuses_state_and_only_writes_changed_content(self):
        directory = self.root / "exports"
        raw = self.store.snapshot()
        with patch.object(
            self.store, "snapshot", side_effect=AssertionError("Unexpected snapshot read")
        ):
            output = view.export_graph(self.store, directory, state=raw)
            timestamp = output.stat().st_mtime_ns
            with patch.object(
                view, "atomic_text", side_effect=AssertionError("Unchanged export rewritten")
            ):
                self.assertEqual(view.export_graph(self.store, directory, state=raw), output)
            self.assertEqual(output.stat().st_mtime_ns, timestamp)
        self.assertEqual(output.name, "research-map.html")
        self.assertNotIn("PRIVATE_CONFIG", output.read_text())
        raw["notes"]["text"] = "A changed research note"
        view.export_graph(self.store, directory, state=raw)
        self.assertIn("A changed research note", output.read_text())
        output.write_bytes(b"\xffcorrupted")
        view.export_graph(self.store, directory, state=raw)
        self.assertIn("A changed research note", output.read_text())

    def test_api_is_fresh_and_routes_cannot_serve_local_files_or_mutate_store(self):
        server = self.start_server()
        self.assertEqual(server.server_address[0], "127.0.0.1")
        status, headers, body = self.get(server)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(json.loads(body)["nodes"], [])
        node = self.propose()
        status, _, body = self.get(server, "/api/graph?fresh=1")
        self.assertEqual(json.loads(body)["nodes"][0]["id"], node["id"])
        before, events = self.store.snapshot(), self.store.events()
        with patch.object(
            self.store, "_mutate", side_effect=AssertionError("Viewer mutated Store")
        ):
            status, headers, html = self.get(server, "/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("const LIVE = true;", html)
            for route in (
                "/.research/state.sqlite3",
                "/../source/draft.txt",
                "/%2e%2e/secret",
                "/api/graph/../secret",
                "/favicon.ico",
                "/assets/research-map.html",
            ):
                self.assertEqual(self.get(server, route)[0], 404, route)
            self.assertEqual(
                self.get(
                    server,
                    "http://other.example/api/graph",
                    headers={"Host": f"127.0.0.1:{server.server_port}",},
                )[0],
                404,
            )
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(self.get(server, method=method)[0], 405)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.events(), events)

    def test_host_allowlist_rejects_rebinding_without_reading_snapshot(self):
        store = Mock()
        server = self.start_server(store)
        for host in (
            "untrusted.example",
            f"untrusted.example:{server.server_port}",
            "127.0.0.1:1",
            "localhost",
        ):
            self.assertEqual(self.get(server, headers={"Host": host})[0], 403, host)
        store.snapshot.assert_not_called()
        store.snapshot.return_value = self.store.snapshot()
        self.assertEqual(
            self.get(server, headers={"Host": f"localhost:{server.server_port}"})[0], 200
        )
        store.snapshot.assert_called_once_with()

    def test_api_failure_is_safe_and_server_recovers(self):
        server = self.start_server()
        with patch.object(
            self.store, "snapshot", side_effect=RuntimeError("PRIVATE_CONFIG /secret/path")
        ):
            status, _, body = self.get(server)
            self.assertEqual(status, 503)
            self.assertNotIn("PRIVATE_CONFIG", body)
            self.assertNotIn("/secret/path", body)
        self.assertEqual(self.get(server)[0], 200)

    def test_serve_closes_on_interrupt_and_browser_failure(self):
        server = Mock(server_port=42123)
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(view, "make_server", return_value=server) as make_server:
            with patch.object(
                view.webbrowser, "open", side_effect=RuntimeError("No browser")
            ) as browser:
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    view.serve_view(self.store)
        make_server.assert_called_once_with(self.store, 0)
        browser.assert_called_once_with("http://127.0.0.1:42123/")
        self.assertEqual(stdout.getvalue().strip(), "http://127.0.0.1:42123/")
        self.assertIn("use the URL", stderr.getvalue())
        server.server_close.assert_called_once_with()

    def test_port_validation_and_no_browser_mode(self):
        for port in (-1, 65536, True, "0"):
            with self.assertRaises(ValueError):
                view.make_server(self.store, port)
        server = Mock(server_port=42124)
        server.serve_forever.side_effect = KeyboardInterrupt
        with patch.object(view, "make_server", return_value=server), patch.object(
            view.webbrowser, "open"
        ) as browser:
            with redirect_stdout(io.StringIO()):
                view.serve_view(self.store, open_browser=False)
            browser.assert_not_called()
        server.server_close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
