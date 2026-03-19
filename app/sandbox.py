import os
import uuid
import shutil
import base64
from pathlib import Path
from typing import Optional


SANDBOX_BASE_DIR = os.environ.get("SANDBOX_DIR", "/sandboxes")


class SandboxNotFoundError(Exception):
    pass


class InvalidPathError(Exception):
    pass


class SandboxManager:
    def __init__(self, base_dir: str = SANDBOX_BASE_DIR):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)

    def create(self, files: Optional[list[dict]] = None) -> str:
        sandbox_id = str(uuid.uuid4())
        sandbox_path = self._get_path(sandbox_id)
        os.makedirs(sandbox_path, exist_ok=True)

        if files:
            for f in files:
                self._write_file(sandbox_path, f)

        return sandbox_id

    def delete(self, sandbox_id: str) -> None:
        path = self._get_path(sandbox_id)
        if not os.path.exists(path):
            raise SandboxNotFoundError(sandbox_id)
        shutil.rmtree(path)

    def exists(self, sandbox_id: str) -> bool:
        return os.path.exists(self._get_path(sandbox_id))

    def list_files(self, sandbox_id: str) -> list[str]:
        path = self._get_path(sandbox_id)
        if not os.path.exists(path):
            raise SandboxNotFoundError(sandbox_id)
        result = []
        for root, _, files in os.walk(path):
            for name in files:
                full = os.path.join(root, name)
                result.append(os.path.relpath(full, path))
        return result

    def load_into_dir(self, sandbox_id: str, target_dir: str) -> None:
        """Copy all sandbox files into target_dir before execution."""
        src = self._get_path(sandbox_id)
        if not os.path.exists(src):
            raise SandboxNotFoundError(sandbox_id)
        for item in os.listdir(src):
            s = os.path.join(src, item)
            d = os.path.join(target_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)

    def _get_path(self, sandbox_id: str) -> str:
        # Prevent path traversal in sandbox IDs
        safe_id = os.path.basename(sandbox_id)
        if safe_id != sandbox_id or not safe_id:
            raise InvalidPathError(f"Invalid sandbox ID: {sandbox_id}")
        return os.path.join(self.base_dir, safe_id)

    def _sanitize_filename(self, name: str) -> str:
        """Prevent path traversal in file names while allowing subdirectories."""
        normalized = os.path.normpath(name)
        if os.path.isabs(normalized):
            raise InvalidPathError(f"Absolute paths not allowed: {name}")
        if normalized.startswith(".."):
            raise InvalidPathError(f"Path traversal not allowed: {name}")
        return normalized

    def _write_file(self, sandbox_path: str, file_info: dict) -> None:
        safe_name = self._sanitize_filename(file_info["name"])
        file_path = os.path.join(sandbox_path, safe_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        encoding = file_info.get("encoding", "text")
        if encoding == "base64":
            content = base64.b64decode(file_info["content"])
            with open(file_path, "wb") as f:
                f.write(content)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(file_info["content"])
