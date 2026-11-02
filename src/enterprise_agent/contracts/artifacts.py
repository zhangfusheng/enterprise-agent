from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ArtifactRef(BaseModel):
    artifact_id: str
    run_id: str
    tenant_id: str
    name: str
    media_type: str
    size_bytes: int
    sha256: str
    path: str
    created_at: datetime
    metadata: dict[str, Any] = {}

