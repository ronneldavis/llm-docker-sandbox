"""Unit tests for WorkerPool (worker.py)."""

import asyncio

import pytest

from worker import ExecTask, WorkerPool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_task(sandbox_id: str, command: str, timeout: int = 10) -> ExecTask:
    return ExecTask(sandbox_id=sandbox_id, command=command, timeout=timeout)


# ---------------------------------------------------------------------------
# Output capture
# ---------------------------------------------------------------------------


class TestOutputCapture:
    async def test_stdout(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "echo hello"))
        assert result.stdout.strip() == "hello"
        assert result.exit_code == 0
        assert result.error is None

    async def test_stderr(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "echo err >&2"))
        assert result.stderr.strip() == "err"
        assert result.stdout == ""

    async def test_both_streams(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(
            make_task(sid, "echo out; echo err >&2")
        )
        assert result.stdout.strip() == "out"
        assert result.stderr.strip() == "err"

    async def test_exit_code_zero(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "true"))
        assert result.exit_code == 0

    async def test_exit_code_nonzero(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "exit 42"))
        assert result.exit_code == 42

    async def test_multiline_stdout(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "printf 'a\\nb\\nc'"))
        assert result.stdout == "a\nb\nc"

    async def test_pipe_command(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(
            make_task(sid, "echo -e 'b\\na\\nc' | sort")
        )
        assert result.stdout.strip() == "a\nb\nc"


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------


class TestFileLoading:
    async def test_files_available_in_execution(self, started_pool, mgr):
        sid = mgr.create([{"name": "data.txt", "content": "sandbox content"}])
        result = await started_pool.submit(make_task(sid, "cat data.txt"))
        assert result.stdout.strip() == "sandbox content"

    async def test_subdirectory_files_available(self, started_pool, mgr):
        sid = mgr.create([{"name": "src/mod.py", "content": "print('mod')"}])
        result = await started_pool.submit(make_task(sid, "python3 src/mod.py"))
        assert result.stdout.strip() == "mod"

    async def test_missing_file_causes_error(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "cat nonexistent.txt"))
        assert result.exit_code != 0

    async def test_ls_shows_loaded_files(self, started_pool, mgr):
        sid = mgr.create([
            {"name": "a.txt", "content": "a"},
            {"name": "b.txt", "content": "b"},
        ])
        result = await started_pool.submit(make_task(sid, "ls"))
        files = result.stdout.split()
        assert "a.txt" in files
        assert "b.txt" in files


# ---------------------------------------------------------------------------
# Immutability — temp dir is fresh each execution
# ---------------------------------------------------------------------------


class TestImmutability:
    async def test_writes_do_not_persist(self, started_pool, mgr):
        sid = mgr.create([{"name": "f.txt", "content": "original"}])
        await started_pool.submit(make_task(sid, "echo mutated > f.txt"))
        result = await started_pool.submit(make_task(sid, "cat f.txt"))
        assert result.stdout.strip() == "original"

    async def test_created_files_do_not_persist(self, started_pool, mgr):
        sid = mgr.create()
        await started_pool.submit(make_task(sid, "touch new_file.txt"))
        result = await started_pool.submit(make_task(sid, "ls"))
        assert "new_file.txt" not in result.stdout

    async def test_deleted_files_reappear(self, started_pool, mgr):
        sid = mgr.create([{"name": "keep.txt", "content": "x"}])
        await started_pool.submit(make_task(sid, "rm keep.txt"))
        result = await started_pool.submit(make_task(sid, "ls"))
        assert "keep.txt" in result.stdout


# ---------------------------------------------------------------------------
# Timeout enforcement
# ---------------------------------------------------------------------------


class TestTimeout:
    async def test_timeout_returns_error(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(
            make_task(sid, "sleep 10", timeout=1)
        )
        assert result.exit_code == -1
        assert result.error is not None
        assert "timed out" in result.error.lower()

    async def test_fast_command_within_timeout(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "echo ok", timeout=5))
        assert result.exit_code == 0

    async def test_timeout_error_field_is_none_on_success(self, started_pool, mgr):
        sid = mgr.create()
        result = await started_pool.submit(make_task(sid, "echo ok"))
        assert result.error is None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_sandbox_not_found(self, started_pool):
        result = await started_pool.submit(
            make_task("aaaaaaaa-0000-0000-0000-000000000000", "ls")
        )
        assert result.exit_code == -1
        assert result.error is not None
        assert "not found" in result.error.lower()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    async def test_all_tasks_complete(self, started_pool, mgr):
        sid = mgr.create()
        tasks = [
            started_pool.submit(make_task(sid, f"echo {i}"))
            for i in range(8)
        ]
        results = await asyncio.gather(*tasks)
        outputs = {r.stdout.strip() for r in results}
        assert outputs == {str(i) for i in range(8)}

    async def test_independent_sandboxes_in_parallel(self, started_pool, mgr):
        sids = [
            mgr.create([{"name": "id.txt", "content": str(i)}])
            for i in range(4)
        ]
        tasks = [
            started_pool.submit(make_task(sid, "cat id.txt"))
            for sid in sids
        ]
        results = await asyncio.gather(*tasks)
        outputs = {r.stdout.strip() for r in results}
        assert outputs == {"0", "1", "2", "3"}

    async def test_queue_depth_returns_to_zero(self, started_pool, mgr):
        sid = mgr.create()
        tasks = [
            started_pool.submit(make_task(sid, "echo x"))
            for _ in range(6)
        ]
        await asyncio.gather(*tasks)
        assert started_pool.queue_size() == 0
