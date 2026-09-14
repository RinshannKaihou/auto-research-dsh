"""Local, interactive project workbench. Mutations require a same-origin capability.

The graph viewer remains usable separately and offline. This server exposes only
named operator actions; it is neither a file server nor a shell command endpoint.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urlsplit
import webbrowser

from .errors import ConflictError, NotFoundError, ResearchError
from .visualization import graph_snapshot, render_html


MAX_BODY = 1024 * 1024


def pick_path(kind: str) -> str | None:
    """Ask macOS to choose one path; only fixed AppleScript is executable here."""
    if kind not in {"file", "folder"}:
        raise ValueError("请选择文件或文件夹")
    if sys.platform != "darwin":
        raise ValueError("此设备不支持系统选择窗口，请直接填写本地路径")
    command = (
        'POSIX path of (choose folder with prompt "选择研究材料或课题文件夹")'
        if kind == "folder"
        else 'POSIX path of (choose file with prompt "选择研究材料")'
    )
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-e", command],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("选择窗口已超时，可以重新选择或填写路径") from error
    if result.returncode:
        if "(-128)" in result.stderr:
            return None
        raise ValueError("未能打开系统选择窗口，请直接填写本地路径")
    return result.stdout.rstrip("\r\n") or None


def render_workbench(boot: dict) -> str:
    template = (
        resources.files("auto_research")
        .joinpath("assets", "research-workbench.html")
        .read_text(encoding="utf-8")
    )
    if template.count("__ARI_GUI_BOOT__") != 1:
        raise ValueError("Workbench template has an invalid boot placeholder")
    data = json.dumps(boot, ensure_ascii=False, allow_nan=False)
    for character, replacement in (
        ("&", "\\u0026"),
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        data = data.replace(character, replacement)
    return template.replace("__ARI_GUI_BOOT__", data)


class _GuiServer(ThreadingHTTPServer):
    daemon_threads = True


def make_gui_server(workbench, port: int = 0, picker=None) -> ThreadingHTTPServer:
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError("工作台端口必须在 0 到 65535 之间")
    chooser = picker or pick_path
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _reply(
            self,
            status: int,
            body: str,
            content_type: str = "application/json; charset=utf-8",
            *,
            download: bool = False,
        ) -> None:
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'",
            )
            if download:
                self.send_header("Content-Disposition", 'attachment; filename="research-map.html"')
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def _json(self, status: int, value: dict) -> None:
            self._reply(status, json.dumps(value, ensure_ascii=False, allow_nan=False))

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _local(self, mutation: bool = False) -> bool:
            allowed = {
                f"127.0.0.1:{self.server.server_port}",
                f"localhost:{self.server.server_port}",
            }
            if self.server.server_port == 80:
                allowed.update({"127.0.0.1", "localhost"})
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0].strip().lower() not in allowed:
                self._error(403, "请通过本机工作台地址访问")
                return False
            if mutation:
                origins = self.headers.get_all("Origin", [])
                capabilities = self.headers.get_all("X-ARI-Token", [])
                if (
                    len(origins) != 1
                    or origins[0] != "http://" + hosts[0].strip().lower()
                    or len(capabilities) != 1
                    or not secrets.compare_digest(
                        capabilities[0].encode("utf-8"), token.encode("ascii")
                    )
                ):
                    self._error(403, "页面会话已失效，请刷新工作台后重试")
                    return False
            return True

        def _route(self):
            try:
                parsed = urlsplit(self.path)
                if parsed.scheme or parsed.netloc:
                    return None, {}
                return parsed.path, parse_qs(parsed.query, max_num_fields=10)
            except ValueError:
                return None, {}

        @staticmethod
        def _id(query: dict, key: str) -> str:
            values = query.get(key, [])
            if len(values) != 1 or not values[0]:
                raise ValueError("请选择要操作的课题")
            return values[0]

        def _failure(self, error: Exception) -> None:
            if isinstance(error, (ConflictError, FileExistsError)):
                self._error(409, str(error))
            elif isinstance(error, (NotFoundError, FileNotFoundError)):
                self._error(404, str(error))
            elif isinstance(error, (ResearchError, ValueError, TypeError)):
                self._error(400, str(error))
            elif isinstance(error, PermissionError):
                self._error(403, "没有权限访问所选目录，请选择可读写的本地目录")
            else:
                self._error(500, "本次操作未完成，请查看课题状态后重试")

        def do_GET(self) -> None:
            if not self._local():
                return
            path, query = self._route()
            try:
                if path == "/":
                    boot = {
                        "token": token,
                        "workspace": str(workbench.workspace),
                        "defaults": workbench.defaults,
                        "projects": workbench.list_projects(),
                    }
                    self._reply(200, render_workbench(boot), "text/html; charset=utf-8")
                elif path == "/api/projects":
                    self._json(
                        200,
                        {
                            "workspace": str(workbench.workspace),
                            "projects": workbench.list_projects(),
                        },
                    )
                elif path == "/api/project":
                    self._json(200, workbench.project_state(self._id(query, "id")))
                elif path in {"/api/graph", "/map", "/export"}:
                    store = workbench.get_store(self._id(query, "project"))
                    state = graph_snapshot(store)
                    if path == "/api/graph":
                        self._json(200, state)
                    else:
                        self._reply(
                            200,
                            render_html(state, live=path == "/map"),
                            "text/html; charset=utf-8",
                            download=path == "/export",
                        )
                else:
                    self._error(404, "此页面不存在")
            except Exception as error:
                self._failure(error)

        def do_POST(self) -> None:
            if not self._local(mutation=True):
                return
            path, query = self._route()
            endpoints = {
                "/api/projects/create",
                "/api/projects/open",
                "/api/project/update",
                "/api/project/action",
                "/api/project/export",
                "/api/pick",
            }
            if path not in endpoints:
                self._error(404, "此操作不存在")
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1:
                self._error(400, "请求格式不正确")
                return
            try:
                size = int(lengths[0])
            except ValueError:
                size = -1
            if size < 1 or size > MAX_BODY:
                self._error(413 if size > MAX_BODY else 400, "提交内容为空或过大")
                return
            if self.headers.get_content_type() != "application/json":
                self._error(415, "请使用工作台表单提交")
                return
            try:
                self.connection.settimeout(10)
                payload = self.rfile.read(size)
                if len(payload) != size:
                    raise ValueError("提交内容不完整")
                data = json.loads(
                    payload.decode("utf-8"),
                    parse_constant=lambda _: (_ for _ in ()).throw(ValueError("数值必须有限")),
                )
                if not isinstance(data, dict):
                    raise ValueError("提交内容必须是表单对象")
                if path == "/api/projects/create":
                    result = workbench.create(data)
                elif path == "/api/projects/open":
                    result = workbench.open(data.get("path"))
                elif path == "/api/project/update":
                    result = workbench.update(
                        data.get("id"), {key: value for key, value in data.items() if key != "id"}
                    )
                elif path == "/api/project/action":
                    result = workbench.action(data.get("id"), data.get("action"))
                elif path == "/api/project/export":
                    project_id = data.get("id")
                    workbench.get_store(project_id)
                    result = {
                        "url": "/export?" + urlencode({"project": project_id}),
                        "filename": "research-map.html",
                    }
                else:
                    kind = data.get("kind")
                    if kind not in {"file", "folder"}:
                        raise ValueError("请选择文件或文件夹")
                    result = {"path": chooser(kind)}
                self._json(200, result)
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._error(400, "表单内容无法解析，请重新填写")
            except TimeoutError:
                self._error(400, "提交内容读取超时，请重试")
            except Exception as error:
                self._failure(error)

        def _not_allowed(self) -> None:
            self._error(405, "此操作不受支持")

        do_PUT = do_PATCH = do_DELETE = _not_allowed

    server = _GuiServer(("127.0.0.1", port), Handler)
    server.ari_token = token
    return server


def serve_gui(workspace: str | Path, port: int = 0, open_browser: bool = True) -> None:
    from .workbench import Workbench

    workbench = Workbench(workspace)
    server = make_gui_server(workbench, port)
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(url, flush=True)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                print("请在浏览器中打开上面的工作台地址。", file=sys.stderr)
        try:
            server.serve_forever(poll_interval=0.2)
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()
        workbench.close()
