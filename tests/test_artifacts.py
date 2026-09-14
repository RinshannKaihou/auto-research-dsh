"""Observable archive and workspace behavior, including concurrent publishers."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from auto_research import artifacts
from auto_research.artifacts import ArtifactError, ArtifactStore


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "research"
        self.store = ArtifactStore(self.root)
        self.root = self.store.root
        self.output = self.root / "source"
        self.output.mkdir()

    def tearDown(self):
        artifacts._remove_owned(Path(self.temporary.name))
        self.temporary.cleanup()

    def file(self, name="proof.txt", content="a partial proof\n"):
        path = self.output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def freeze(self, path=None):
        return self.store.freeze(path or self.file(), self.output)

    def assert_no_unpublished(self):
        self.assertFalse(list(self.store.objects.glob(".stage-*")))

    def test_file_digest_and_archive_are_independent_of_source(self):
        source = self.file()
        product = self.freeze(source)
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        self.assertEqual(
            product, {"path": f".research/objects/{expected}", "version": expected, "kind": "file",}
        )
        archive = self.store.verify(product)
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o444)
        source.write_text("continue the proof")
        self.assertEqual(archive.read_text(), "a partial proof\n")
        self.store.verify(product)
        self.assert_no_unpublished()

    def test_directory_hash_includes_names_content_and_empty_directories(self):
        self.file("z.txt", "Z")
        self.file("child/a.txt", "A")
        (self.output / "empty").mkdir()
        records = [
            ["directory", "child"],
            ["file", "child/a.txt", hashlib.sha256(b"A").hexdigest()],
            ["directory", "empty"],
            ["file", "z.txt", hashlib.sha256(b"Z").hexdigest()],
        ]
        encoding = "auto-research-directory-v1\n" + "".join(
            json.dumps(record, separators=(",", ":")) + "\n" for record in records
        )
        product = self.freeze(self.output)
        self.assertEqual(product["version"], hashlib.sha256(encoding.encode()).hexdigest())
        self.assertEqual(product["kind"], "directory")
        archive = self.store.verify(product)
        self.assertTrue((archive / "empty").is_dir())
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o555)
        (self.output / "empty").rmdir()
        self.assertNotEqual(product["version"], self.freeze(self.output)["version"])
        (self.output / "z.txt").rename(self.output / "renamed.txt")
        self.assertNotEqual(product["version"], self.freeze(self.output)["version"])

    def test_empty_directory_unicode_and_newlines_are_stable(self):
        empty = self.freeze(self.output)
        self.assertEqual(
            empty["version"], hashlib.sha256(b"auto-research-directory-v1\n").hexdigest()
        )
        self.file("证明\n.txt", "unfinished")
        first = self.freeze(self.output)
        os.utime(self.output / "证明\n.txt", (1000, 1000))
        self.assertEqual(first, self.freeze(self.output))

    def test_identical_content_is_reused_without_rewriting_it(self):
        source = self.file()
        product = self.freeze(source)
        archive = self.store.verify(product)
        inode = archive.stat().st_ino
        second_source = self.file("second.txt")
        self.assertEqual(product, self.freeze(second_source))
        self.assertEqual(archive.stat().st_ino, inode)
        self.assert_no_unpublished()

    def test_concurrent_publication_deduplicates_files_and_directories(self):
        source = self.file()
        for target in (source, self.output):
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: self.freeze(target), range(12)))
            self.assertTrue(all(item == results[0] for item in results))
            self.store.verify(results[0])
        self.assert_no_unpublished()

    def test_rejects_outside_paths_parent_traversal_and_missing_sources(self):
        external = Path(self.temporary.name) / "outside"
        external.write_text("not authorized")
        for path in (external, self.output / ".." / "source", self.output / "missing"):
            with self.subTest(path=path), self.assertRaises(ArtifactError):
                self.freeze(path)
        self.assert_no_unpublished()

    def test_relative_source_and_system_root_alias(self):
        self.file()
        self.assertEqual(self.freeze("proof.txt"), self.freeze(self.output / "proof.txt"))
        with tempfile.TemporaryDirectory(dir="/tmp") as raw:
            store = ArtifactStore(raw)
            source = Path(raw) / "source.txt"
            source.write_text("alias")
            product = store.freeze(source, Path(raw))
            copy = store.materialize(product, Path(raw) / "copy.txt")
            self.assertEqual(copy.read_text(), "alias")
            artifacts._remove_owned(Path(raw))

    def test_rejects_symlinks_in_source_root_parents_and_tree(self):
        self.file()
        (self.output / "link").symlink_to(self.output / "proof.txt")
        with self.assertRaises(ArtifactError):
            self.freeze(self.output / "link")
        with self.assertRaises(ArtifactError):
            self.freeze(self.output)
        (self.output / "link").unlink()
        (self.output / "alias").symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(ArtifactError):
            self.freeze(self.output / "alias" / "proof.txt")
        self.assert_no_unpublished()

    def test_rejects_git_metadata_and_special_files(self):
        self.file("nested/.git/config")
        with self.assertRaises(ArtifactError):
            self.freeze(self.output)
        with self.assertRaises(ArtifactError):
            self.freeze(self.output / "nested/.git/config")
        artifacts._remove_owned(self.output / "nested")
        os.mkfifo(self.output / "pipe")
        with self.assertRaises(ArtifactError):
            self.freeze(self.output)
        with self.assertRaises(ArtifactError):
            self.freeze(self.output / "pipe")
        self.assert_no_unpublished()

    def test_source_changed_during_copy_is_not_published(self):
        source = self.file(content="first")
        original = artifacts.os.read
        changed = False

        def read_then_mutate(*args):
            nonlocal changed
            chunk = original(*args)
            if chunk and not changed:
                changed = True
                source.write_text("later")
            return chunk

        with patch.object(artifacts.os, "read", side_effect=read_then_mutate):
            with self.assertRaisesRegex(ArtifactError, "changed"):
                self.freeze(source)
        self.assert_no_unpublished()
        self.assertFalse([path for path in self.store.objects.iterdir() if len(path.name) == 64])

    def test_source_replaced_after_read_is_not_published(self):
        source = self.file()
        original = artifacts._transfer

        def replace_after_copy(*args, **kwargs):
            result = original(*args, **kwargs)
            source.rename(self.output / "old")
            source.write_text("replacement")
            return result

        with patch.object(artifacts, "_transfer", side_effect=replace_after_copy):
            with self.assertRaisesRegex(ArtifactError, "changed|replaced"):
                self.freeze(source)
        self.assert_no_unpublished()

    def test_directory_changed_after_copy_is_not_published(self):
        self.file()
        original = artifacts._transfer

        def add_after_copy(*args, **kwargs):
            result = original(*args, **kwargs)
            if len(args) < 4:
                self.file("unexpected.txt")
            return result

        with patch.object(artifacts, "_transfer", side_effect=add_after_copy):
            with self.assertRaisesRegex(ArtifactError, "changed"):
                self.freeze(self.output)
        self.assert_no_unpublished()

    def test_corruption_is_detected_without_overwriting_existing_object(self):
        source = self.file()
        product = self.freeze(source)
        archive = self.store.verify(product)
        archive.chmod(0o600)
        archive.write_text("corrupted")
        with self.assertRaisesRegex(ArtifactError, "corrupted"):
            self.store.verify(product)
        with self.assertRaisesRegex(ArtifactError, "corrupted"):
            self.freeze(source)
        self.assertEqual(archive.read_text(), "corrupted")

    def test_directory_corruption_and_symlink_replacement_are_detected(self):
        self.file()
        product = self.freeze(self.output)
        archive = self.store.verify(product)
        file = archive / "proof.txt"
        file.chmod(0o600)
        file.write_text("changed")
        with self.assertRaises(ArtifactError):
            self.store.verify(product)
        archive.chmod(0o700)
        file.unlink()
        file.symlink_to(self.output / "proof.txt")
        with self.assertRaises(ArtifactError):
            self.store.verify(product)

    def test_reference_shape_and_metadata_symlink_are_rejected(self):
        product = self.freeze()
        for changes in (
            {"version": "abc"},
            {"version": "A" * 64},
            {"path": "../outside"},
            {"kind": "finding"},
            {"kind": "directory"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ArtifactError):
                self.store.verify({**product, **changes})
        moved = self.root / "original-objects"
        self.store.objects.rename(moved)
        self.store.objects.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(ArtifactError):
            self.store.verify(product)
        with self.assertRaises(ArtifactError):
            ArtifactStore(self.root)
        with self.assertRaises(ArtifactError):
            self.freeze()

    def test_materialize_readonly_or_writable_without_sharing_archive(self):
        product = self.freeze()
        readonly = self.store.materialize(product, self.root / "copy.txt")
        writable = self.store.materialize(product, self.root / "continuation.txt", readonly=False)
        self.assertEqual(stat.S_IMODE(readonly.stat().st_mode), 0o444)
        writable.write_text("next stage")
        self.assertEqual(self.store.verify(product).read_text(), "a partial proof\n")
        self.assertEqual(readonly.read_text(), "a partial proof\n")
        with self.assertRaises(ArtifactError):
            self.store.materialize(product, readonly)
        directory = self.freeze(self.output)
        copy = self.store.materialize(directory, self.root / "tree")
        self.assertEqual((copy / "proof.txt").read_text(), "a partial proof\n")

    def test_materialize_rejects_symlink_and_outside_destinations(self):
        product = self.freeze()
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.root / "redirect").symlink_to(outside, target_is_directory=True)
        for dest in (
            self.root / "redirect" / "file",
            outside / "file",
            "../outside/file",
            self.root / ".research/state.sqlite3",
        ):
            with self.subTest(dest=dest), self.assertRaises(ArtifactError):
                self.store.materialize(product, dest)
        self.assertFalse(list(outside.iterdir()))

    def test_materialize_detects_change_between_verification_and_copy(self):
        product = self.freeze()
        original = self.store.verify

        def verify_then_change(value):
            source = original(value)
            source.chmod(0o600)
            source.write_text("changed after verification")
            return source

        with patch.object(self.store, "verify", side_effect=verify_then_change):
            with self.assertRaisesRegex(ArtifactError, "changed during materialization"):
                self.store.materialize(product, self.root / "copy")
        self.assertFalse((self.root / "copy").exists())
        self.assertFalse(list(self.root.glob(".materialize-*")))

    def test_concurrent_same_destination_does_not_replace_first_copy(self):
        product = self.freeze()

        def copy(_):
            try:
                return self.store.materialize(product, self.root / "copy")
            except ArtifactError:
                return None

        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(copy, range(5)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual((self.root / "copy").read_text(), "a partial proof\n")

    def test_workspace_multiple_inputs_keep_identity_and_are_independent(self):
        first = self.freeze()
        second = self.freeze(self.file("other.txt", "another branch"))
        inputs = [
            {"node_id": "X-1", "product_id": "proof", "product": first},
            {"node_id": "X-2", "product_id": "proof", "product": second},
        ]
        with ThreadPoolExecutor(max_workers=4) as pool:
            workspaces = list(
                pool.map(lambda i: self.store.prepare_workspace(f"try-{i}", inputs), range(4))
            )
        self.assertEqual(len(set(workspaces)), 4)
        for workspace in workspaces:
            self.assertEqual((workspace / "inputs/X-1/proof").read_text(), "a partial proof\n")
            self.assertEqual((workspace / "inputs/X-2/proof").read_text(), "another branch")
            self.assertEqual(stat.S_IMODE((workspace / "inputs").stat().st_mode), 0o555)
            (workspace / "scratch/probe.txt").write_text(workspace.name)
            (workspace / "output/draft.txt").write_text("incomplete")
        self.assertFalse((workspaces[1] / "scratch/other.txt").exists())
        self.store.verify(first)
        self.store.verify(second)

    def test_workspace_rejects_duplicate_or_invalid_identifiers_and_cleans_failure(self):
        for identifier in ("../escape", "a/b", ".", "", None):
            with self.subTest(identifier=identifier), self.assertRaises(ArtifactError):
                self.store.prepare_workspace(identifier)
        product = self.freeze()
        item = {"node_id": "X-1", "product_id": "proof", "product": product}
        with self.assertRaisesRegex(ArtifactError, "Duplicate"):
            self.store.prepare_workspace("duplicate", [item, item])
        self.assertFalse((self.root / "workspaces/duplicate").exists())
        self.store.prepare_workspace("unique")
        with self.assertRaisesRegex(ArtifactError, "already exists"):
            self.store.prepare_workspace("unique")
        for key in ("node_id", "product_id"):
            with self.subTest(key=key), self.assertRaises(ArtifactError):
                self.store.prepare_workspace("invalid", [{**item, key: "../escape"}])
            self.assertFalse((self.root / "workspaces/invalid").exists())

    def test_workspace_accepts_resolved_or_flattened_products(self):
        product = self.freeze()
        for attempt, item in (
            ("nested", {"node_id": "X-1", "id": "proof", "item": product}),
            ("flat", {"node_id": "X-1", "id": "proof", **product}),
        ):
            workspace = self.store.prepare_workspace(attempt, [item])
            self.assertEqual((workspace / "inputs/X-1/proof").read_text(), "a partial proof\n")

    def test_workspaces_symlink_cannot_redirect_execution(self):
        external = Path(self.temporary.name) / "external"
        external.mkdir()
        (self.root / "workspaces").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ArtifactError):
            self.store.prepare_workspace("escape")
        self.assertFalse(list(external.iterdir()))

    def git_repository(self):
        repository = Path(self.temporary.name) / "code"
        repository.mkdir()
        self.git(repository, "init", "-b", "main")
        (repository / "math.py").write_text("x = 1\n")
        self.git(repository, "add", "math.py")
        self.git(
            repository,
            "-c",
            "user.name=Tests",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-m",
            "initial",
        )
        return repository

    def git(self, repository, *arguments):
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments], capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def test_git_worktrees_are_independent_and_preserve_source_checkout(self):
        repository = self.git_repository()
        before = self.git(repository, "rev-parse", "HEAD")
        with ThreadPoolExecutor(max_workers=2) as pool:
            workspaces = list(
                pool.map(
                    lambda i: self.store.prepare_workspace(f"git-{i}", source_git=repository),
                    range(2),
                )
            )
        for workspace in workspaces:
            project = workspace / "project"
            self.assertEqual(self.git(project, "rev-parse", "HEAD"), before)
            self.assertTrue(self.git(project, "branch", "--show-current").startswith("codex/ari-"))
        (workspaces[0] / "project/math.py").write_text("x = 2\n")
        self.assertEqual((repository / "math.py").read_text(), "x = 1\n")
        self.assertEqual((workspaces[1] / "project/math.py").read_text(), "x = 1\n")
        self.assertEqual(self.git(repository, "branch", "--show-current"), "main")
        self.assertEqual(self.git(repository, "status", "--porcelain"), "")

    def test_dirty_git_source_and_invalid_repository_leave_no_workspace(self):
        repository = self.git_repository()
        (repository / "math.py").write_text("uncommitted")
        with self.assertRaisesRegex(ArtifactError, "uncommitted"):
            self.store.prepare_workspace("dirty", source_git=repository)
        self.assertFalse((self.root / "workspaces/dirty").exists())
        with self.assertRaisesRegex(ArtifactError, "Git worktree operation failed"):
            self.store.prepare_workspace("invalid", source_git=self.output)
        self.assertFalse((self.root / "workspaces/invalid").exists())

    def test_git_failure_cleanup_and_timeout_are_explicit(self):
        repository = self.git_repository()
        original = self.store._git

        def failing_add(repo, *args, **kwargs):
            if args[:2] == ("worktree", "add"):
                raise ArtifactError("injected worktree failure")
            return original(repo, *args, **kwargs)

        with patch.object(self.store, "_git", side_effect=failing_add):
            with self.assertRaisesRegex(ArtifactError, "injected"):
                self.store.prepare_workspace("failed", source_git=repository)
        self.assertFalse((self.root / "workspaces/failed").exists())
        with patch.object(
            artifacts.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 60)
        ):
            with self.assertRaisesRegex(ArtifactError, "Git worktree operation failed"):
                self.store._git(repository, "status")


if __name__ == "__main__":
    unittest.main()
