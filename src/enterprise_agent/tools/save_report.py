import hashlib
from datetime import UTC
from pathlib import Path

from enterprise_agent.contracts.artifacts import ArtifactRef


class ReportStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._by_operation: dict[str, ArtifactRef] = {}

    def save(self, tenant_id: str, run_id: str, markdown: str, operation_id: str, name: str = "report.md") -> ArtifactRef:
        if operation_id in self._by_operation:
            return self._by_operation[operation_id]
        safe_name = Path(name).name
        if not safe_name.endswith(".md"):
            safe_name += ".md"
        directory = self.root / tenant_id / run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / safe_name
        payload = markdown.encode("utf-8")
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        from datetime import datetime
        artifact = ArtifactRef(
            artifact_id=f"artifact_{run_id}_{digest[:12]}",
            run_id=run_id,
            tenant_id=tenant_id,
            name=safe_name,
            media_type="text/markdown",
            size_bytes=len(payload),
            sha256=digest,
            path=str(path),
            created_at=datetime.now(UTC),
        )
        self._by_operation[operation_id] = artifact
        return artifact

    def for_run(self, run_id: str) -> list[ArtifactRef]:
        return [item for item in self._by_operation.values() if item.run_id == run_id]

