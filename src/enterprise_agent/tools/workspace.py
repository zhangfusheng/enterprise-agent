import os
from pathlib import Path


class WorkspaceDenied(PermissionError):
    pass


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe(self, relative: str) -> Path:
        if os.path.isabs(relative):
            raise WorkspaceDenied("absolute paths are not allowed")
        path = (self.root / relative).resolve()
        if path != self.root and self.root not in path.parents:
            raise WorkspaceDenied("path escapes workspace")
        if path.exists() and path.is_symlink():
            raise WorkspaceDenied("symlinks are not allowed")
        for parent in path.parents:
            if parent == self.root:
                break
            if parent.is_symlink():
                raise WorkspaceDenied("symlink escape is not allowed")
        return path

    def read(self, relative: str, max_bytes: int = 200_000) -> str:
        path = self._safe(relative)
        data = path.read_bytes()
        if len(data) > max_bytes:
            raise ValueError("workspace file exceeds size limit")
        return data.decode("utf-8")

    def search(self, query: str, max_files: int = 100) -> list[dict[str, str]]:
        if not query:
            return []
        matches: list[dict[str, str]] = []
        for path in self.root.rglob("*"):
            if len(matches) >= max_files:
                break
            if not path.is_file() or path.is_symlink():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for line_number, line in enumerate(content.splitlines(), 1):
                if query.lower() in line.lower():
                    matches.append({"path": str(path.relative_to(self.root)), "line": str(line_number), "text": line[:500]})
        return matches

