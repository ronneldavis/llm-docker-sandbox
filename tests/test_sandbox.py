"""Unit tests for SandboxManager (sandbox.py)."""

import base64
from pathlib import Path

import pytest

from sandbox import InvalidPathError, SandboxManager, SandboxNotFoundError


# ---------------------------------------------------------------------------
# create()
# ---------------------------------------------------------------------------


class TestCreate:
    def test_returns_uuid_string(self, mgr):
        sid = mgr.create()
        assert isinstance(sid, str)
        assert len(sid) == 36  # xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

    def test_each_call_produces_unique_id(self, mgr):
        ids = {mgr.create() for _ in range(20)}
        assert len(ids) == 20

    def test_directory_is_created(self, mgr, sandbox_dir):
        sid = mgr.create()
        assert (sandbox_dir / sid).is_dir()

    def test_no_files_gives_empty_dir(self, mgr, sandbox_dir):
        sid = mgr.create()
        assert list((sandbox_dir / sid).iterdir()) == []

    def test_text_file_written(self, mgr, sandbox_dir):
        sid = mgr.create([{"name": "hello.txt", "content": "world"}])
        assert (sandbox_dir / sid / "hello.txt").read_text() == "world"

    def test_multiple_files_written(self, mgr, sandbox_dir):
        sid = mgr.create([
            {"name": "a.txt", "content": "aaa"},
            {"name": "b.txt", "content": "bbb"},
        ])
        assert (sandbox_dir / sid / "a.txt").read_text() == "aaa"
        assert (sandbox_dir / sid / "b.txt").read_text() == "bbb"

    def test_base64_file_written(self, mgr, sandbox_dir):
        raw = b"\x00\x01\x02\xff binary data"
        sid = mgr.create([{
            "name": "data.bin",
            "content": base64.b64encode(raw).decode(),
            "encoding": "base64",
        }])
        assert (sandbox_dir / sid / "data.bin").read_bytes() == raw

    def test_subdirectory_file_created(self, mgr, sandbox_dir):
        sid = mgr.create([{"name": "src/utils.py", "content": "x = 1"}])
        assert (sandbox_dir / sid / "src" / "utils.py").read_text() == "x = 1"

    def test_deeply_nested_file(self, mgr, sandbox_dir):
        sid = mgr.create([{"name": "a/b/c/deep.txt", "content": "deep"}])
        assert (sandbox_dir / sid / "a" / "b" / "c" / "deep.txt").read_text() == "deep"

    def test_path_traversal_absolute_rejected(self, mgr):
        with pytest.raises(InvalidPathError):
            mgr.create([{"name": "/etc/passwd", "content": "bad"}])

    def test_path_traversal_dotdot_rejected(self, mgr):
        with pytest.raises(InvalidPathError):
            mgr.create([{"name": "../../etc/passwd", "content": "bad"}])

    def test_path_traversal_dotdot_in_subdir_rejected(self, mgr):
        with pytest.raises(InvalidPathError):
            mgr.create([{"name": "safe/../../../etc/passwd", "content": "bad"}])


# ---------------------------------------------------------------------------
# exists()
# ---------------------------------------------------------------------------


class TestExists:
    def test_true_after_create(self, mgr):
        sid = mgr.create()
        assert mgr.exists(sid) is True

    def test_false_for_unknown_id(self, mgr):
        assert mgr.exists("aaaaaaaa-0000-0000-0000-000000000000") is False

    def test_false_after_delete(self, mgr):
        sid = mgr.create()
        mgr.delete(sid)
        assert mgr.exists(sid) is False


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


class TestDelete:
    def test_removes_directory(self, mgr, sandbox_dir):
        sid = mgr.create()
        mgr.delete(sid)
        assert not (sandbox_dir / sid).exists()

    def test_raises_for_unknown_id(self, mgr):
        with pytest.raises(SandboxNotFoundError):
            mgr.delete("does-not-exist")

    def test_raises_on_double_delete(self, mgr):
        sid = mgr.create()
        mgr.delete(sid)
        with pytest.raises(SandboxNotFoundError):
            mgr.delete(sid)


# ---------------------------------------------------------------------------
# list_files()
# ---------------------------------------------------------------------------


class TestListFiles:
    def test_empty_sandbox(self, mgr):
        sid = mgr.create()
        assert mgr.list_files(sid) == []

    def test_lists_top_level_files(self, mgr):
        sid = mgr.create([
            {"name": "a.txt", "content": "a"},
            {"name": "b.txt", "content": "b"},
        ])
        assert sorted(mgr.list_files(sid)) == ["a.txt", "b.txt"]

    def test_lists_nested_files(self, mgr):
        sid = mgr.create([{"name": "src/utils.py", "content": "x"}])
        files = mgr.list_files(sid)
        assert len(files) == 1
        assert "utils.py" in files[0]

    def test_raises_for_unknown(self, mgr):
        with pytest.raises(SandboxNotFoundError):
            mgr.list_files("nope")


# ---------------------------------------------------------------------------
# load_into_dir()
# ---------------------------------------------------------------------------


class TestLoadIntoDir:
    def test_copies_single_file(self, mgr, tmp_path):
        sid = mgr.create([{"name": "f.txt", "content": "hello"}])
        dest = tmp_path / "run"
        dest.mkdir()
        mgr.load_into_dir(sid, str(dest))
        assert (dest / "f.txt").read_text() == "hello"

    def test_copies_nested_files(self, mgr, tmp_path):
        sid = mgr.create([{"name": "src/x.py", "content": "x=1"}])
        dest = tmp_path / "run"
        dest.mkdir()
        mgr.load_into_dir(sid, str(dest))
        assert (dest / "src" / "x.py").read_text() == "x=1"

    def test_stored_file_unchanged_after_copy_mutation(self, mgr, tmp_path):
        """Mutating a copied file must not alter the stored original."""
        sid = mgr.create([{"name": "f.txt", "content": "original"}])
        for i in range(3):
            dest = tmp_path / f"run_{i}"
            dest.mkdir()
            mgr.load_into_dir(sid, str(dest))
            (dest / "f.txt").write_text("mutated")

        stored_path = Path(mgr._get_path(sid)) / "f.txt"
        assert stored_path.read_text() == "original"

    def test_raises_for_unknown(self, mgr, tmp_path):
        with pytest.raises(SandboxNotFoundError):
            mgr.load_into_dir("nope", str(tmp_path))
