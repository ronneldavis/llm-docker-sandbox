"""Integration tests for the FastAPI REST API (main.py)."""

import asyncio
import base64

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def create_sandbox(client, files=None):
    """Helper: POST /sandbox and return the sandbox UUID."""
    r = await client.post("/sandbox", json={"files": files or []})
    assert r.status_code == 201
    return r.json()["id"]


async def exec_cmd(client, sandbox_id, command, timeout=None):
    """Helper: POST /sandbox/{id}/exec and return the JSON response."""
    body = {"command": command}
    if timeout is not None:
        body["timeout"] = timeout
    r = await client.post(f"/sandbox/{sandbox_id}/exec", json=body)
    return r


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_status_ok(self, client):
        r = await client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    async def test_reports_worker_count(self, client):
        r = await client.get("/health")
        assert r.json()["workers"] == 2  # fixture creates 2 workers

    async def test_queue_size_field_present(self, client):
        r = await client.get("/health")
        assert "queue_size" in r.json()


# ---------------------------------------------------------------------------
# POST /sandbox
# ---------------------------------------------------------------------------


class TestCreateSandbox:
    async def test_returns_201_and_id(self, client):
        r = await client.post("/sandbox", json={})
        assert r.status_code == 201
        data = r.json()
        assert "id" in data
        assert len(data["id"]) == 36

    async def test_empty_files_list(self, client):
        r = await client.post("/sandbox", json={"files": []})
        assert r.status_code == 201

    async def test_with_text_file(self, client):
        r = await client.post("/sandbox", json={
            "files": [{"name": "hello.txt", "content": "hi"}]
        })
        assert r.status_code == 201
        assert "id" in r.json()

    async def test_with_multiple_files(self, client):
        r = await client.post("/sandbox", json={
            "files": [
                {"name": "a.txt", "content": "a"},
                {"name": "b.txt", "content": "b"},
                {"name": "src/c.py", "content": "c=1"},
            ]
        })
        assert r.status_code == 201

    async def test_with_base64_file(self, client):
        encoded = base64.b64encode(b"\x00\x01\x02binary").decode()
        r = await client.post("/sandbox", json={
            "files": [{"name": "data.bin", "content": encoded, "encoding": "base64"}]
        })
        assert r.status_code == 201

    async def test_invalid_encoding_rejected(self, client):
        r = await client.post("/sandbox", json={
            "files": [{"name": "f.txt", "content": "x", "encoding": "latin-1"}]
        })
        assert r.status_code == 422

    async def test_path_traversal_rejected(self, client):
        r = await client.post("/sandbox", json={
            "files": [{"name": "../../etc/passwd", "content": "bad"}]
        })
        assert r.status_code == 400

    async def test_absolute_path_rejected(self, client):
        r = await client.post("/sandbox", json={
            "files": [{"name": "/etc/passwd", "content": "bad"}]
        })
        assert r.status_code == 400

    async def test_each_sandbox_gets_unique_id(self, client):
        ids = {(await client.post("/sandbox", json={})).json()["id"] for _ in range(5)}
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# GET /sandbox/{id}
# ---------------------------------------------------------------------------


class TestGetSandbox:
    async def test_returns_id_and_files(self, client):
        sid = await create_sandbox(client, [
            {"name": "a.txt", "content": "a"},
            {"name": "b.txt", "content": "b"},
        ])
        r = await client.get(f"/sandbox/{sid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == sid
        assert sorted(data["files"]) == ["a.txt", "b.txt"]

    async def test_empty_sandbox_has_empty_files(self, client):
        sid = await create_sandbox(client)
        r = await client.get(f"/sandbox/{sid}")
        assert r.json()["files"] == []

    async def test_not_found_for_unknown_id(self, client):
        r = await client.get("/sandbox/aaaaaaaa-0000-0000-0000-000000000000")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /sandbox/{id}/exec
# ---------------------------------------------------------------------------


class TestExec:
    async def test_stdout_captured(self, client):
        sid = await create_sandbox(client)
        r = await exec_cmd(client, sid, "echo hello")
        assert r.status_code == 200
        data = r.json()
        assert data["stdout"].strip() == "hello"
        assert data["exit_code"] == 0
        assert data["error"] is None

    async def test_stderr_captured(self, client):
        sid = await create_sandbox(client)
        r = await exec_cmd(client, sid, "echo err >&2")
        assert r.json()["stderr"].strip() == "err"

    async def test_exit_code_propagated(self, client):
        sid = await create_sandbox(client)
        r = await exec_cmd(client, sid, "exit 7")
        assert r.json()["exit_code"] == 7

    async def test_files_accessible(self, client):
        sid = await create_sandbox(client, [{"name": "msg.txt", "content": "from file"}])
        r = await exec_cmd(client, sid, "cat msg.txt")
        assert r.json()["stdout"].strip() == "from file"

    async def test_subdirectory_files_accessible(self, client):
        sid = await create_sandbox(client, [{"name": "src/app.py", "content": "print(99)"}])
        r = await exec_cmd(client, sid, "python3 src/app.py")
        assert r.json()["stdout"].strip() == "99"

    async def test_base64_file_content_correct(self, client):
        encoded = base64.b64encode(b"binary payload").decode()
        sid = await create_sandbox(client, [
            {"name": "data.bin", "content": encoded, "encoding": "base64"}
        ])
        r = await exec_cmd(client, sid, "cat data.bin")
        assert r.json()["stdout"] == "binary payload"

    async def test_python_script_runs(self, client):
        sid = await create_sandbox(client, [
            {"name": "calc.py", "content": "print(2 ** 10)"}
        ])
        r = await exec_cmd(client, sid, "python3 calc.py")
        assert r.json()["stdout"].strip() == "1024"

    async def test_multifile_python_import(self, client):
        sid = await create_sandbox(client, [
            {"name": "lib.py", "content": "def greet(): return 'hi'"},
            {"name": "main.py", "content": "from lib import greet; print(greet())"},
        ])
        r = await exec_cmd(client, sid, "python3 main.py")
        assert r.json()["stdout"].strip() == "hi"

    async def test_pipe_command(self, client):
        sid = await create_sandbox(client)
        r = await exec_cmd(client, sid, "echo -e 'b\\na' | sort")
        assert r.json()["stdout"].strip() == "a\nb"

    async def test_ls_shows_sandbox_files(self, client):
        sid = await create_sandbox(client, [
            {"name": "one.txt", "content": ""},
            {"name": "two.txt", "content": ""},
        ])
        r = await exec_cmd(client, sid, "ls")
        files = r.json()["stdout"].split()
        assert "one.txt" in files
        assert "two.txt" in files

    # --- Immutability ---

    async def test_file_writes_do_not_persist(self, client):
        sid = await create_sandbox(client, [{"name": "f.txt", "content": "original"}])
        await exec_cmd(client, sid, "echo changed > f.txt")
        r = await exec_cmd(client, sid, "cat f.txt")
        assert r.json()["stdout"].strip() == "original"

    async def test_created_files_do_not_persist(self, client):
        sid = await create_sandbox(client)
        await exec_cmd(client, sid, "touch temp.txt")
        r = await exec_cmd(client, sid, "ls")
        assert "temp.txt" not in r.json()["stdout"]

    # --- Timeout ---

    async def test_timeout_enforced(self, client):
        sid = await create_sandbox(client)
        r = await exec_cmd(client, sid, "sleep 10", timeout=1)
        data = r.json()
        assert data["exit_code"] == -1
        assert "timed out" in (data["error"] or "").lower()

    async def test_invalid_timeout_rejected(self, client):
        sid = await create_sandbox(client)
        r = await client.post(f"/sandbox/{sid}/exec", json={"command": "ls", "timeout": 0})
        assert r.status_code == 422

    async def test_negative_timeout_rejected(self, client):
        sid = await create_sandbox(client)
        r = await client.post(f"/sandbox/{sid}/exec", json={"command": "ls", "timeout": -1})
        assert r.status_code == 422

    # --- Error handling ---

    async def test_sandbox_not_found_returns_404(self, client):
        r = await client.post(
            "/sandbox/aaaaaaaa-0000-0000-0000-000000000000/exec",
            json={"command": "ls"},
        )
        assert r.status_code == 404

    # --- Concurrency ---

    async def test_concurrent_requests_all_succeed(self, client):
        sid = await create_sandbox(client)
        tasks = [exec_cmd(client, sid, f"echo {i}") for i in range(8)]
        responses = await asyncio.gather(*tasks)
        outputs = {r.json()["stdout"].strip() for r in responses}
        assert outputs == {str(i) for i in range(8)}

    async def test_concurrent_independent_sandboxes(self, client):
        pairs = []
        for i in range(4):
            sid = await create_sandbox(client, [{"name": "id.txt", "content": str(i)}])
            pairs.append((sid, str(i)))

        tasks = [exec_cmd(client, sid, "cat id.txt") for sid, _ in pairs]
        responses = await asyncio.gather(*tasks)
        outputs = {r.json()["stdout"].strip() for r in responses}
        assert outputs == {"0", "1", "2", "3"}


# ---------------------------------------------------------------------------
# DELETE /sandbox/{id}
# ---------------------------------------------------------------------------


class TestDeleteSandbox:
    async def test_delete_returns_200_and_id(self, client):
        sid = await create_sandbox(client)
        r = await client.delete(f"/sandbox/{sid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == sid
        assert data["deleted"] is True

    async def test_get_returns_404_after_delete(self, client):
        sid = await create_sandbox(client)
        await client.delete(f"/sandbox/{sid}")
        r = await client.get(f"/sandbox/{sid}")
        assert r.status_code == 404

    async def test_exec_returns_404_after_delete(self, client):
        sid = await create_sandbox(client)
        await client.delete(f"/sandbox/{sid}")
        r = await exec_cmd(client, sid, "ls")
        assert r.status_code == 404

    async def test_delete_unknown_returns_404(self, client):
        r = await client.delete("/sandbox/aaaaaaaa-0000-0000-0000-000000000000")
        assert r.status_code == 404

    async def test_double_delete_returns_404(self, client):
        sid = await create_sandbox(client)
        await client.delete(f"/sandbox/{sid}")
        r = await client.delete(f"/sandbox/{sid}")
        assert r.status_code == 404
