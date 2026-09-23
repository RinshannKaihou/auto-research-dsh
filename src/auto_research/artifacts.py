"""Fixed research products and private execution directories.

Files are identified by SHA-256 of their bytes. Directory identities hash the
UTF-8 domain prefix ``auto-research-directory-v1\\n`` followed by one compact,
ASCII JSON record and newline per descendant, sorted by relative POSIX path.
Records are ``["directory", path]`` or ``["file", path, file_sha256]``. Empty
directories therefore count; timestamps and permission bits do not. Archived
files are data (mode 0444), so execute scripts through their interpreter.

Readonly modes prevent accidental writes, not hostile writes by the same Unix
user. The execution backend must deny workers access to the metadata store and
must restrict writes to each worker's own permitted directories.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
from typing import Any, Iterator
import uuid


class ArtifactError(ValueError):
    """An unsafe, inconsistent, or unavailable research product."""


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_DIR_FLAGS = _READ_FLAGS | os.O_DIRECTORY


def _signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _kind(info: os.stat_result) -> str:
    if stat.S_ISREG(info.st_mode):
        return "file"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    raise ArtifactError("Artifacts may contain only regular files and directories")


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ArtifactError(f"Unsafe {label}: {value!r}")
    return value


def _safe_directory(path: Path) -> None:
    """Create one trusted path component without following an existing link."""
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ArtifactError(f"Expected a real directory: {path}") from None


def _source_relative(path: str | Path, allowed_root: str | Path) -> tuple[Path, Path]:
    raw = Path(path)
    boundary = Path(allowed_root).resolve(strict=True)
    if ".." in raw.parts or ".git" in raw.parts:
        raise ArtifactError("Parent traversal and .git are not allowed in artifact paths")
    absolute = raw if raw.is_absolute() else boundary / raw
    # /tmp is a system symlink on macOS. Find the first spelling of the trusted
    # root, then inspect every component below it without following symlinks.
    for ancestor in reversed((absolute, *absolute.parents)):
        if ancestor.resolve(strict=True) == boundary:
            return boundary, absolute.relative_to(ancestor)
    raise ArtifactError(f"Artifact is outside its allowed directory: {path}")


@contextlib.contextmanager
def _open_source(path: str | Path, allowed_root: str | Path) -> Iterator[int]:
    boundary, relative = _source_relative(path, allowed_root)
    descriptors: list[int] = []
    try:
        descriptor = os.open(boundary, _DIR_FLAGS)
        descriptors.append(descriptor)
        for index, name in enumerate(relative.parts):
            flags = _READ_FLAGS if index == len(relative.parts) - 1 else _DIR_FLAGS
            descriptor = os.open(name, flags, dir_fd=descriptor)
            descriptors.append(descriptor)
        before = _signature(os.fstat(descriptor))
        yield descriptor
        if _signature(os.fstat(descriptor)) != before:
            raise ArtifactError("Artifact source changed while being read")
        if relative.parts:
            latest = os.stat(relative.name, dir_fd=descriptors[-2], follow_symlinks=False)
            if _signature(latest) != before:
                raise ArtifactError("Artifact source was replaced while being read")
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _inventory(descriptor: int, relative: str = "") -> dict[str, tuple[int, ...]]:
    info = os.fstat(descriptor)
    kind = _kind(info)
    result = {relative: _signature(info)}
    if kind == "directory":
        for name in sorted(os.listdir(descriptor)):
            if name == ".git":
                raise ArtifactError("Git metadata must not be archived as a research product")
            name.encode("utf-8", errors="strict")
            child_info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            _kind(child_info)
            child = os.open(name, _READ_FLAGS, dir_fd=descriptor)
            try:
                if _signature(os.fstat(child)) != _signature(child_info):
                    raise ArtifactError("Artifact changed while its files were being listed")
                child_path = f"{relative}/{name}" if relative else name
                result.update(_inventory(child, child_path))
            finally:
                os.close(child)
    return result


def _transfer(
    descriptor: int,
    inventory: dict[str, tuple[int, ...]],
    destination: Path | None,
    relative: str = "",
) -> list[list[str]]:
    if _signature(os.fstat(descriptor)) != inventory.get(relative):
        raise ArtifactError("Artifact source changed before it could be copied")
    kind = _kind(os.fstat(descriptor))
    records: list[list[str]] = []
    if kind == "directory":
        if destination is not None:
            destination.mkdir(mode=0o700)
        if relative:
            records.append(["directory", relative])
        for name in sorted(os.listdir(descriptor)):
            child = os.open(name, _READ_FLAGS, dir_fd=descriptor)
            try:
                child_relative = f"{relative}/{name}" if relative else name
                child_destination = destination / name if destination is not None else None
                records.extend(_transfer(child, inventory, child_destination, child_relative))
            finally:
                os.close(child)
        if destination is not None:
            _sync_directory(destination)
    else:
        digest = hashlib.sha256()
        output = destination.open("xb") if destination is not None else contextlib.nullcontext()
        with output as stream:
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                if stream is not None:
                    stream.write(chunk)
            if stream is not None:
                stream.flush()
                os.fsync(stream.fileno())
        records.append(["file", relative, digest.hexdigest()])
    if _signature(os.fstat(descriptor)) != inventory.get(relative):
        raise ArtifactError("Artifact source changed while it was being copied")
    return records


def _snapshot(
    path: str | Path, allowed_root: str | Path, destination: Path | None = None
) -> tuple[str, str]:
    try:
        with _open_source(path, allowed_root) as descriptor:
            kind = _kind(os.fstat(descriptor))
            before = _inventory(descriptor)
            records = _transfer(descriptor, before, destination)
            if _inventory(descriptor) != before:
                raise ArtifactError("Artifact source changed during the snapshot")
        if kind == "file":
            return kind, records[0][2]
        digest = hashlib.sha256(b"auto-research-directory-v1\n")
        for record in sorted(records, key=lambda entry: entry[1]):
            digest.update(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode())
            digest.update(b"\n")
        return kind, digest.hexdigest()
    except (OSError, UnicodeError) as exc:
        raise ArtifactError(f"Cannot safely read artifact {path}: {exc}") from exc


def _readonly(path: Path) -> None:
    if path.is_dir():
        for child in path.iterdir():
            _readonly(child)
        path.chmod(0o555)
    else:
        path.chmod(0o444)


def _remove_owned(path: Path) -> None:
    """Remove an unpublished snapshot or newly created workspace we own."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        path.chmod(0o700)
        for child in path.iterdir():
            _remove_owned(child)
        path.rmdir()
    else:
        path.unlink()


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, _DIR_FLAGS)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class ArtifactStore:
    """Content addressed archives belonging to a single research project."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.meta = self.root / ".research"
        self.objects = self.meta / "objects"
        _safe_directory(self.meta)
        _safe_directory(self.objects)

    def freeze(self, path: str | Path, allowed_root: str | Path) -> dict[str, str]:
        """Publish an unchanged file/tree; identical existing objects are reused.

        The caller must choose a private output directory as ``allowed_root``.
        External datasets should remain explicit source references instead of
        being passed here. No database mutation occurs in this method.
        """
        _safe_directory(self.meta)
        _safe_directory(self.objects)
        stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.objects))
        payload = stage / "payload"
        try:
            kind, version = _snapshot(path, allowed_root, payload)
            _readonly(payload)
            product = {
                "path": f".research/objects/{version}",
                "version": version,
                "kind": kind,
            }
            with _lock(self.objects / ".publish.lock"):
                destination = self.objects / version
                if destination.exists() or destination.is_symlink():
                    self.verify(product)
                else:
                    # macOS requires write permission on a directory when its
                    # parent changes during rename (updating the '..' entry).
                    if kind == "directory":
                        payload.chmod(0o700)
                    os.rename(payload, destination)
                    if kind == "directory":
                        destination.chmod(0o555)
                    _sync_directory(self.objects)
            return product
        finally:
            _remove_owned(stage)

    def verify(self, product: dict[str, Any]) -> Path:
        """Check a canonical archive reference and its complete content digest."""
        version = product.get("version")
        if not isinstance(version, str) or not _DIGEST.fullmatch(version):
            raise ArtifactError("Artifact version must be a complete lowercase SHA-256 digest")
        if product.get("path") != f".research/objects/{version}":
            raise ArtifactError("Artifact path must identify its content-addressed archive")
        if product.get("kind") not in {"file", "directory"}:
            raise ArtifactError("Artifact kind must be file or directory")
        path = self.root / product["path"]
        kind, actual = _snapshot(path, self.root)
        if kind != product["kind"] or actual != version:
            raise ArtifactError(f"Archived artifact is corrupted: {version}")
        return path

    def materialize(
        self, product: dict[str, Any], destination: str | Path, readonly: bool = True
    ) -> Path:
        """Copy a verified product without changing its archive or overwriting files."""
        source = self.verify(product)
        subpath = product.get("subpath")
        if subpath:
            # The parser has already resolved this path. Check again here because
            # materialize is also a public API, then snapshot only this subtree.
            if not isinstance(subpath, str) or Path(subpath).is_absolute() or any(
                part in ("", "..") for part in subpath.split("/")
            ):
                raise ArtifactError("Invalid frozen object subpath")
            source = source / subpath
        try:
            _, relative = _source_relative(destination, self.root)
        except OSError as exc:
            raise ArtifactError(f"Invalid materialization destination: {destination}") from exc
        if relative.parts and relative.parts[0] == ".research":
            raise ArtifactError("Materialization cannot write research metadata or fixed archives")
        destination = self.root / relative
        if destination.exists() or destination.is_symlink():
            raise ArtifactError(f"Materialization destination already exists: {destination}")
        # Destinations are private caller-owned directories, but pre-existing
        # symlinks below the project root must never redirect a write elsewhere.
        current = self.root
        for name in relative.parts[:-1]:
            current = current / name
            _safe_directory(current)
        temporary = destination.parent / f".materialize-{uuid.uuid4().hex}"
        try:
            kind, version = _snapshot(source, self.root, temporary)
            if not subpath and (version != product["version"] or kind != product["kind"]):
                raise ArtifactError("Archived artifact changed during materialization")
            if readonly:
                _readonly(temporary)
            # A per-store lock also protects concurrent attempts at the same
            # destination from replacing each other's completed input copies.
            with _lock(self.objects / ".publish.lock"):
                if destination.exists() or destination.is_symlink():
                    raise ArtifactError(
                        f"Materialization destination already exists: {destination}"
                    )
                os.rename(temporary, destination)
                _sync_directory(destination.parent)
            return destination
        finally:
            _remove_owned(temporary)

    def prepare_workspace(
        self,
        attempt_id: str,
        inputs: list[dict[str, Any]] | None = None,
        source_git: str | Path | None = None,
    ) -> Path:
        """Create independent inputs/, scratch/, output/, and optional project/.

        Inputs have ``node_id``, ``product_id``, and ``product`` fields. The
        optional Git worktree uses the source repository's committed HEAD;
        dirty source checkouts are rejected to avoid silently losing changes.
        Worktree branches have unique ``codex/ari-...`` names. No source commit,
        staging, reset, checkout, or merge is performed.
        """
        attempt_id = _identifier(attempt_id, "attempt identifier")
        workspaces = self.root / "workspaces"
        _safe_directory(workspaces)
        workspace = workspaces / attempt_id
        try:
            workspace.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ArtifactError(f"Workspace already exists: {attempt_id}") from exc
        git_created = False
        branch: str | None = None
        repository: Path | None = None
        try:
            for directory in ("inputs", "scratch", "output"):
                (workspace / directory).mkdir(mode=0o700)
            seen: set[tuple[str, str]] = set()
            for item in inputs or []:
                node_id = _identifier(item.get("node_id"), "input node identifier")
                product_id = _identifier(
                    item.get("product_id", item.get("id")), "input product identifier"
                )
                pair = (node_id, product_id)
                if pair in seen:
                    raise ArtifactError(f"Duplicate workspace input: {node_id}/{product_id}")
                seen.add(pair)
                product = item.get("product", item.get("item", item))
                self.materialize(product, workspace / "inputs" / node_id / product_id)
            _readonly(workspace / "inputs")
            if source_git is not None:
                repository = Path(source_git).resolve(strict=True)
                with _lock(self.objects / ".git.lock"):
                    status = self._git(repository, "status", "--porcelain", "--untracked-files=all")
                    if status.strip():
                        raise ArtifactError(
                            "Git source has uncommitted changes; use a fixed snapshot"
                        )
                    branch = f"codex/ari-{attempt_id}-{uuid.uuid4().hex[:12]}"
                    self._git(
                        repository,
                        "worktree",
                        "add",
                        "-b",
                        branch,
                        str(workspace / "project"),
                        "HEAD",
                    )
                    git_created = True
            return workspace
        except BaseException:
            if repository is not None and branch is not None:
                with _lock(self.objects / ".git.lock"):
                    if git_created:
                        self._git(
                            repository, "worktree", "remove", "--force", str(workspace / "project")
                        )
                    # This unique branch can exist even if worktree add failed.
                    self._git(repository, "branch", "-D", branch, check=False)
            _remove_owned(workspace)
            raise

    @staticmethod
    def _git(repository: Path, *arguments: str, check: bool = True) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *arguments],
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArtifactError(f"Git worktree operation failed: {exc}") from exc
        if check and result.returncode:
            raise ArtifactError(f"Git worktree operation failed: {result.stderr.strip()}")
        return result.stdout
