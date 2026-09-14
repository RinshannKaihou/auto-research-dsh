"""HTTP control and isolation tests; no real research processes or file dialogs."""

import copy
import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from auto_research import gui


class FakeStore:
    def __init__(self):
        self.reads = 0
        self.state = {
            "project": {
                "goal": "A conditional argument",
                "budget": 100,
                "created_at": "2026-09-13T00:00:00Z",
                "config": {"backend_command": "PRIVATE_BACKEND_CONFIG"},
            },
            "agenda": {"version": 1, "text": "Check the missing assumption", "questions": []},
            "notes": {"version": 1, "text": "No conclusion yet"},
            "nodes": [],
            "attempts": [
                {
                    "id": "A-001",
                    "node_id": None,
                    "role": "coordinator",
                    "state": "reserved",
                    "cost": None,
                    "cost_kind": "unknown",
                    "job_dir": "PRIVATE_JOB_DIRECTORY",
                    "prompt": "PRIVATE_AGENT_PROMPT",
                }
            ],
            "budget": {"total": 100, "spent": 0, "held": 0, "unknown_count": 0},
        }

    def snapshot(self):
        self.reads += 1
        return copy.deepcopy(self.state)


class FakeWorkbench:
    """Record the HTTP boundary's calls, without delegating to any runtime."""

    def __init__(self, workspace):
        self.workspace = Path(workspace)
        self.defaults = {"budget": 100, "parallel": 2, "backend": "dsh"}
        self.projects = [{"id": "P-001", "name": "Local argument", "status": "paused"}]
        self.calls = []
        self.store = FakeStore()
        self.failure = None

    def _record(self, method, *args):
        self.calls.append((method, *copy.deepcopy(args)))
        if self.failure:
            raise self.failure

    def list_projects(self):
        self._record("list_projects")
        return copy.deepcopy(self.projects)

    def project_state(self, identifier):
        self._record("project_state", identifier)
        return {"id": identifier, "status": "paused", "graph": self.store.snapshot()}

    def get_store(self, identifier):
        self._record("get_store", identifier)
        return self.store

    def create(self, body):
        self._record("create", body)
        return {"id": "P-created", "created": True}

    def open(self, project_path):
        self._record("open", project_path)
        return {"id": "P-opened", "opened": True}

    def update(self, identifier, body):
        self._record("update", identifier, body)
        return {"id": identifier, "updated": True}

    def action(self, identifier, action):
        self._record("action", identifier, action)
        return {"id": identifier, "action": action}

    def close(self):
        self.calls.append(("close",))


class GuiHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workbench = FakeWorkbench(self.root)
        self.picker = Mock(return_value=str(self.root / "chosen"))
        self.server = self.start_server(self.workbench, self.picker)
        self.host = f"127.0.0.1:{self.server.server_port}"
        self.origin = f"http://{self.host}"

    def start_server(self, workbench, picker):
        server = gui.make_gui_server(workbench, port=0, picker=picker)
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

    def request(self, route, method="GET", body=None, headers=None, authenticated=False):
        outgoing = {}
        if authenticated:
            outgoing.update(
                {
                    "Host": self.host,
                    "Origin": self.origin,
                    "Content-Type": "application/json",
                    "X-ARI-Token": self.server.ari_token,
                }
            )
        for name, value in (headers or {}).items():
            if value is None:
                outgoing.pop(name, None)
            else:
                outgoing[name] = value
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, route, body=body, headers=outgoing)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def post(self, route, body, headers=None):
        return self.request(route, "POST", body, headers, authenticated=True)

    def raw_request(self, method, route, headers, body=b""):
        """Keep duplicate or missing headers intact instead of using a mapping."""
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.putrequest(method, route, skip_host=True, skip_accept_encoding=True)
            for name, value in headers:
                connection.putheader(name, value)
            connection.endheaders(body)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def assert_error(self, response, status):
        actual, headers, body = response
        self.assertEqual(actual, status, body)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertIsInstance(json.loads(body).get("error"), str)
        self.assertNotIn("Traceback", body)

    def assert_no_mutations(self):
        mutations = {"create", "open", "update", "action", "close"}
        self.assertFalse([call for call in self.workbench.calls if call[0] in mutations])

    def test_loopback_nonce_and_boot_are_safe_and_self_contained(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertIsInstance(self.server.ari_token, str)
        self.assertGreaterEqual(len(self.server.ari_token), 16)
        other = self.start_server(FakeWorkbench(self.root), Mock(return_value=None))
        self.assertNotEqual(self.server.ari_token, other.ari_token)
        attack = '</script><script id="escaped">alert(1)</script>&\u2028'
        self.workbench.defaults["note"] = attack
        package = self.root / "template-package"
        (package / "assets").mkdir(parents=True)
        (package / "assets" / "research-workbench.html").write_text(
            '<!doctype html><script type="application/json" id="ari-gui-boot">'
            "__ARI_GUI_BOOT__</script>",
            encoding="utf-8",
        )
        with patch.object(gui.resources, "files", return_value=package):
            status, headers, document = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(json.dumps(self.server.ari_token), document)
        self.assertNotIn("__ARI_GUI_BOOT__", document)
        self.assertNotIn('<script id="escaped">', document)
        self.assertIn("\\u003c/script\\u003e", document)
        boot = json.loads(re.search(r'id="ari-gui-boot">(.*?)</script>', document, re.DOTALL)[1])
        self.assertEqual(
            boot,
            {
                "token": self.server.ari_token,
                "workspace": str(self.workbench.workspace),
                "defaults": self.workbench.defaults,
                "projects": self.workbench.projects,
            },
        )
        csp = headers.get("Content-Security-Policy", "")
        self.assertRegex(csp, r"frame-src\s+'self'")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assert_no_mutations()

    def test_get_projects_and_project_state_are_read_only_and_fresh(self):
        status, headers, body = self.request("/api/projects")
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            {"workspace": str(self.workbench.workspace), "projects": self.workbench.projects,},
        )
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.workbench.projects.append({"id": "P-002", "name": "A new argument"})
        self.assertEqual(len(json.loads(self.request("/api/projects")[2])["projects"]), 2)
        status, _, body = self.request("/api/project?id=P-001")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["id"], "P-001")
        self.assertIn(("project_state", "P-001"), self.workbench.calls)
        self.assert_no_mutations()

    def test_graph_map_and_download_do_not_write_or_start_research(self):
        before_files = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        before_state = copy.deepcopy(self.workbench.store.state)
        status, _, body = self.request("/api/graph?project=P-001")
        self.assertEqual(status, 200)
        self.assertIn("edges", json.loads(body))
        self.assertNotIn("PRIVATE_", body)
        status, _, live = self.request("/map?project=P-001")
        self.assertEqual(status, 200)
        self.assertRegex(live, r"const\s+LIVE\s*=\s*true")
        self.assertNotIn("PRIVATE_", live)
        status, headers, offline = self.request("/export?project=P-001")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        self.assertIn("research-map.html", headers.get("Content-Disposition", ""))
        self.assertRegex(offline, r"const\s+LIVE\s*=\s*false")
        self.assertNotIn("PRIVATE_", offline)
        self.assertEqual(self.workbench.store.state, before_state)
        self.assertEqual(
            sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*")), before_files
        )
        self.assert_no_mutations()

    def test_authenticated_create_open_update_and_actions_dispatch_exact_arguments(self):
        creation = {"name": "Fresh study", "goal": "Resolve a missing condition", "budget": 30}
        status, _, body = self.post("/api/projects/create", creation)
        self.assertIn(status, (200, 201))
        self.assertEqual(json.loads(body), {"id": "P-created", "created": True})
        project_path = str(self.root / "existing")
        status, _, _ = self.post("/api/projects/open", {"path": project_path})
        self.assertEqual(status, 200)
        update = {"id": "P-001", "goal": "A refined goal", "parallel": 3}
        self.assertEqual(self.post("/api/project/update", update)[0], 200)
        for action in ("start", "pause", "resume"):
            status, _, body = self.post("/api/project/action", {"id": "P-001", "action": action})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"id": "P-001", "action": action})
        self.assertEqual(
            self.workbench.calls,
            [
                ("create", creation),
                ("open", project_path),
                ("update", "P-001", {"goal": "A refined goal", "parallel": 3}),
                ("action", "P-001", "start"),
                ("action", "P-001", "pause"),
                ("action", "P-001", "resume"),
            ],
        )

    def test_picker_is_injected_and_export_action_only_returns_download_url(self):
        for kind in ("folder", "file"):
            status, _, body = self.post("/api/pick", {"kind": kind})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {"path": str(self.root / "chosen")})
        self.assertEqual(
            [call.args for call in self.picker.call_args_list], [("folder",), ("file",)]
        )
        self.picker.return_value = None
        self.assertEqual(json.loads(self.post("/api/pick", {"kind": "folder"})[2]), {"path": None})
        previous_calls = self.picker.call_count
        self.assert_error(self.post("/api/pick", {"kind": 'folder"; arbitrary script'}), 400)
        self.assertEqual(self.picker.call_count, previous_calls)
        before = list(self.root.rglob("*"))
        status, _, body = self.post("/api/project/export", {"id": "P-001"})
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body), {"url": "/export?project=P-001", "filename": "research-map.html",}
        )
        self.assertEqual(list(self.root.rglob("*")), before)
        self.assert_no_mutations()

    def test_missing_or_wrong_origin_and_nonce_reject_before_workbench_or_picker(self):
        cases = (
            {"Origin": None},
            {"Origin": "null"},
            {"Origin": "https://evil.example"},
            {"Origin": f"https://{self.host}"},
            {"Origin": "http://127.0.0.1:1"},
            {"Origin": f"http://localhost:{self.server.server_port}"},
            {"Origin": self.origin + "/untrusted"},
            {"X-ARI-Token": None},
            {"X-ARI-Token": "incorrect"},
            {"X-ARI-Token": "caf\u00e9"},
            {"Origin": None, "X-ARI-Token": None},
        )
        for headers in cases:
            with self.subTest(headers=headers):
                self.assert_error(
                    self.post("/api/projects/create", {"goal": "do not start"}, headers), 403
                )
                self.assert_error(self.post("/api/pick", {"kind": "folder"}, headers), 403)
        self.assertEqual(self.workbench.calls, [])
        self.picker.assert_not_called()

    def test_host_is_exact_and_rebinding_does_not_read_or_mutate(self):
        for host in (
            "evil.example",
            f"evil.example:{self.server.server_port}",
            "127.0.0.1:1",
            "localhost",
        ):
            with self.subTest(host=host):
                self.assert_error(self.request("/api/projects", headers={"Host": host}), 403)
                self.assert_error(
                    self.post(
                        "/api/project/action", {"id": "P-001", "action": "start"}, {"Host": host}
                    ),
                    403,
                )
        self.assertEqual(self.workbench.calls, [])
        localhost = f"localhost:{self.server.server_port}"
        self.assertEqual(self.request("/api/projects", headers={"Host": localhost})[0], 200)
        self.assertEqual(
            self.post(
                "/api/project/action",
                {"id": "P-001", "action": "pause"},
                {"Host": localhost, "Origin": f"http://{localhost}",},
            )[0],
            200,
        )

    def test_invalid_body_and_framing_are_rejected_without_dispatch(self):
        for payload in (
            b"\xff",
            b"{",
            b"null",
            b"[]",
            b'"text"',
            b"true",
            b"42",
            b'{"budget": NaN}',
            b'{"budget": Infinity}',
        ):
            with self.subTest(payload=payload):
                self.assert_error(self.post("/api/projects/create", payload), 400)
        self.assert_error(
            self.post("/api/projects/create", {}, {"Content-Type": "text/plain"}), 415
        )
        self.assert_error(self.post("/api/projects/create", {}, {"Content-Type": None}), 415)
        self.assert_error(
            self.post("/api/projects/create", b"", {"Content-Length": "1048577"}), 413
        )
        for length in ("-1", "not-a-number"):
            self.assert_error(
                self.post("/api/projects/create", b"", {"Content-Length": length}), 400
            )
        self.assert_error(
            self.post("/api/projects/create", b"0\r\n\r\n", {"Transfer-Encoding": "chunked",}), 400
        )
        self.assert_error(
            self.post("/api/projects/create", {}, {"Transfer-Encoding": "identity"}), 400
        )
        self.assertEqual(self.workbench.calls, [])

    def test_missing_or_duplicate_security_and_length_headers_are_rejected(self):
        self.assert_error(self.raw_request("GET", "/api/projects", []), 403)
        self.assert_error(
            self.raw_request(
                "GET", "/api/projects", [("Host", self.host), ("Host", "evil.example"),]
            ),
            403,
        )
        base = [
            ("Host", self.host),
            ("Origin", self.origin),
            ("X-ARI-Token", self.server.ari_token),
            ("Content-Type", "application/json"),
            ("Content-Length", "2"),
        ]
        for name, value, status in (
            ("Origin", self.origin, 403),
            ("X-ARI-Token", self.server.ari_token, 403),
            ("Content-Length", "2", 400),
        ):
            self.assert_error(
                self.raw_request("POST", "/api/projects/create", base + [(name, value)], b"{}",),
                status,
            )
        without_length = [pair for pair in base if pair[0] != "Content-Length"]
        self.assert_error(
            self.raw_request("POST", "/api/projects/create", without_length, b"{}"), 400
        )
        self.assertEqual(self.workbench.calls, [])
        self.picker.assert_not_called()

    def test_unknown_routes_and_write_methods_cannot_open_local_files(self):
        paths = (
            "/.research/state.sqlite3",
            "/../secret",
            "/%2e%2e/secret",
            "/api/projects/../secret",
            "/assets/research-gui.html",
            "/favicon.ico",
            "/api/not-a-route",
        )
        for route in paths:
            self.assert_error(self.request(route), 404)
        self.assert_error(self.post("/api/not-a-route", {}), 404)
        for method in ("PUT", "DELETE"):
            self.assert_error(self.request("/api/projects/create", method=method, body={}), 405)
        self.assertEqual(self.workbench.calls, [])
        self.picker.assert_not_called()

    def test_workbench_errors_are_json_and_leave_server_usable(self):
        self.workbench.failure = ValueError("A required research field is missing")
        status, headers, body = self.post("/api/projects/create", {})
        self.assertEqual(status, 400)
        self.assertIn("application/json", headers["Content-Type"])
        self.assertIsInstance(json.loads(body).get("error"), str)
        self.assertNotIn("Traceback", body)
        self.workbench.failure = RuntimeError("unexpected failure")
        status, headers, body = self.request("/api/projects")
        self.assertEqual(status, 500)
        self.assertIsInstance(json.loads(body).get("error"), str)
        self.assertNotIn("Traceback", body)
        self.workbench.failure = None
        self.assertEqual(self.request("/api/projects")[0], 200)


if __name__ == "__main__":
    unittest.main()
