import asyncio
import subprocess
import sys
import tempfile
import os
import resource
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from sandbox import SandboxManager, SandboxNotFoundError

DEFAULT_TIMEOUT = int(os.environ.get("EXEC_TIMEOUT", "30"))
# Memory limit per execution in bytes (default 256 MB)
MEM_LIMIT_BYTES = int(os.environ.get("MEM_LIMIT_MB", "256")) * 1024 * 1024


@dataclass
class ExecTask:
    sandbox_id: str
    command: str
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    error: Optional[str] = None


def _set_child_limits() -> None:
    """Called in child process after fork — sets resource limits.

    Only used on Linux.  On macOS, fork() called from a thread (inside
    asyncio's ThreadPoolExecutor) can deadlock due to C-library lock
    inheritance, so we skip preexec_fn there entirely.
    """
    try:
        # Cap virtual memory
        resource.setrlimit(resource.RLIMIT_AS, (MEM_LIMIT_BYTES, MEM_LIMIT_BYTES))
        # Cap number of open files
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    except Exception:
        pass  # Best-effort; don't break execution if limits can't be set


# preexec_fn is safe on Linux; skip on macOS to avoid fork-safety deadlocks
# when subprocess is spawned from a ThreadPoolExecutor thread.
_PREEXEC_FN = _set_child_limits if sys.platform == "linux" else None


class WorkerPool:
    def __init__(self, num_workers: int, sandbox_manager: SandboxManager):
        self.num_workers = num_workers
        self.sandbox_manager = sandbox_manager
        self.queue: asyncio.Queue = asyncio.Queue()
        self._executor = ThreadPoolExecutor(max_workers=num_workers)
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for _ in range(self.num_workers):
            asyncio.create_task(self._worker_loop())

    async def submit(self, task: ExecTask) -> ExecResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        await self.queue.put((task, future))
        return await future

    def queue_size(self) -> int:
        return self.queue.qsize()

    async def _worker_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            task, future = await self.queue.get()
            try:
                result = await loop.run_in_executor(
                    self._executor, self._run_task, task
                )
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.queue.task_done()

    def _run_task(self, task: ExecTask) -> ExecResult:
        """Blocking — runs in thread pool. Creates a temp dir, loads sandbox
        files, then executes the command inside it."""
        try:
            if not self.sandbox_manager.exists(task.sandbox_id):
                raise SandboxNotFoundError(task.sandbox_id)
        except SandboxNotFoundError:
            return ExecResult(
                stdout="",
                stderr="",
                exit_code=-1,
                error=f"Sandbox '{task.sandbox_id}' not found",
            )

        with tempfile.TemporaryDirectory(prefix="sbx_") as tmpdir:
            # Load the sandbox files fresh for every execution
            self.sandbox_manager.load_into_dir(task.sandbox_id, tmpdir)

            try:
                proc = subprocess.run(
                    task.command,
                    shell=True,
                    executable="/bin/bash",
                    cwd=tmpdir,
                    capture_output=True,
                    text=True,
                    timeout=task.timeout,
                    stdin=subprocess.DEVNULL,
                    preexec_fn=_PREEXEC_FN,
                )
                return ExecResult(
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    exit_code=proc.returncode,
                    error=None,
                )
            except subprocess.TimeoutExpired:
                return ExecResult(
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    error=f"Command timed out after {task.timeout}s",
                )
            except Exception as exc:
                return ExecResult(
                    stdout="",
                    stderr="",
                    exit_code=-1,
                    error=str(exc),
                )
