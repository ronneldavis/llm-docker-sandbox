import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from sandbox import SandboxManager, SandboxNotFoundError, InvalidPathError
from worker import WorkerPool, ExecTask, DEFAULT_TIMEOUT

NUM_WORKERS = int(os.environ.get("NUM_WORKERS", "4"))

sandbox_manager = SandboxManager()
worker_pool = WorkerPool(NUM_WORKERS, sandbox_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await worker_pool.start()
    yield


app = FastAPI(
    title="Sandbox Execution API",
    description="Create sandboxed environments and execute shell commands via REST.",
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SandboxFile(BaseModel):
    name: str
    content: str
    encoding: str = "text"  # "text" or "base64"

    @field_validator("encoding")
    @classmethod
    def validate_encoding(cls, v: str) -> str:
        if v not in ("text", "base64"):
            raise ValueError("encoding must be 'text' or 'base64'")
        return v


class CreateSandboxRequest(BaseModel):
    files: Optional[list[SandboxFile]] = None


class CreateSandboxResponse(BaseModel):
    id: str


class ExecRequest(BaseModel):
    command: str
    timeout: Optional[int] = None

    @field_validator("timeout")
    @classmethod
    def validate_timeout(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("timeout must be a positive integer")
        return v


class ExecResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    error: Optional[str] = None


class SandboxInfoResponse(BaseModel):
    id: str
    files: list[str]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/sandbox", response_model=CreateSandboxResponse, status_code=201)
async def create_sandbox(request: CreateSandboxRequest):
    """Create a new sandbox, optionally pre-loading files. Returns a UUID."""
    try:
        files = [f.model_dump() for f in request.files] if request.files else None
        sandbox_id = sandbox_manager.create(files)
    except InvalidPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return CreateSandboxResponse(id=sandbox_id)


@app.get("/sandbox/{sandbox_id}", response_model=SandboxInfoResponse)
async def get_sandbox(sandbox_id: str):
    """Return metadata and file listing for an existing sandbox."""
    if not sandbox_manager.exists(sandbox_id):
        raise HTTPException(status_code=404, detail="Sandbox not found")
    files = sandbox_manager.list_files(sandbox_id)
    return SandboxInfoResponse(id=sandbox_id, files=files)


@app.post("/sandbox/{sandbox_id}/exec", response_model=ExecResponse)
async def execute_command(sandbox_id: str, request: ExecRequest):
    """
    Execute a shell command inside the sandbox environment.

    The sandbox files are loaded fresh into a temporary directory before
    each execution. The task is queued and processed by the worker pool.
    """
    if not sandbox_manager.exists(sandbox_id):
        raise HTTPException(status_code=404, detail="Sandbox not found")

    timeout = request.timeout or DEFAULT_TIMEOUT
    task = ExecTask(
        sandbox_id=sandbox_id,
        command=request.command,
        timeout=timeout,
    )

    result = await worker_pool.submit(task)
    return ExecResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code,
        error=result.error,
    )


@app.delete("/sandbox/{sandbox_id}", status_code=200)
async def delete_sandbox(sandbox_id: str):
    """Delete a sandbox and all its associated files."""
    try:
        sandbox_manager.delete(sandbox_id)
    except SandboxNotFoundError:
        raise HTTPException(status_code=404, detail="Sandbox not found")
    return {"id": sandbox_id, "deleted": True}


@app.get("/health")
async def health():
    """Health check — also reports worker count and current queue depth."""
    return {
        "status": "ok",
        "workers": NUM_WORKERS,
        "queue_size": worker_pool.queue_size(),
    }
