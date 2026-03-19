import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient, ASGITransport

# Must be set before app modules are imported so that main.py's module-level
# SandboxManager() call doesn't attempt to create /sandboxes (read-only here).
os.environ.setdefault("SANDBOX_DIR", tempfile.mkdtemp(prefix="test_sbx_init_"))
os.environ.setdefault("NUM_WORKERS", "2")
os.environ.setdefault("EXEC_TIMEOUT", "10")

# Make app modules importable without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

from sandbox import SandboxManager  # noqa: E402
from worker import WorkerPool  # noqa: E402


# ---------------------------------------------------------------------------
# Base fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox_dir(tmp_path):
    d = tmp_path / "sandboxes"
    d.mkdir()
    return d


@pytest.fixture
def mgr(sandbox_dir):
    """A fresh SandboxManager backed by a temp directory."""
    return SandboxManager(str(sandbox_dir))


@pytest.fixture
async def started_pool(mgr):
    """A running WorkerPool (2 workers) for direct worker tests."""
    pool = WorkerPool(num_workers=2, sandbox_manager=mgr)
    await pool.start()
    return pool


# ---------------------------------------------------------------------------
# API test client
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(mgr):
    """
    Async HTTPX client wired to the FastAPI app with a fresh SandboxManager
    and WorkerPool injected via module-level patching.

    We explicitly call pool.start() here because ASGITransport does not
    guarantee triggering FastAPI's lifespan in all httpx versions.
    start() is idempotent, so a double-call from the lifespan is harmless.
    """
    import main

    pool = WorkerPool(num_workers=2, sandbox_manager=mgr)
    await pool.start()
    with patch.object(main, "sandbox_manager", mgr), patch.object(
        main, "worker_pool", pool
    ):
        async with AsyncClient(
            transport=ASGITransport(app=main.app),
            base_url="http://test",
        ) as c:
            yield c
