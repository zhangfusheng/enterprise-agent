from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from enterprise_agent.contracts.artifacts import ArtifactRef
from enterprise_agent.infrastructure.database.models import ArtifactRow


class ArtifactRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def add(self, artifact: ArtifactRef) -> None:
        with self.session_factory() as session:
            if _stored(session, artifact):
                return
            session.add(_to_row(artifact))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                if not _stored(session, artifact):
                    raise

    def list(self, tenant_id: str, run_id: str) -> list[ArtifactRef]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(ArtifactRow)
                .where(ArtifactRow.tenant_id == tenant_id, ArtifactRow.run_id == run_id)
                .order_by(ArtifactRow.created_at)
            ).all()
            return [_to_ref(row) for row in rows]


def _stored(session: Session, artifact: ArtifactRef) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    ArtifactRow.artifact_id == artifact.artifact_id,
                    ArtifactRow.tenant_id == artifact.tenant_id,
                )
            )
        )
    )


def _to_row(artifact: ArtifactRef) -> ArtifactRow:
    return ArtifactRow(
        artifact_id=artifact.artifact_id,
        run_id=artifact.run_id,
        tenant_id=artifact.tenant_id,
        name=artifact.name,
        media_type=artifact.media_type,
        size_bytes=artifact.size_bytes,
        sha256=artifact.sha256,
        path=artifact.path,
        metadata_json=artifact.metadata,
        created_at=artifact.created_at,
    )


def _to_ref(row: ArtifactRow) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=row.artifact_id,
        run_id=row.run_id,
        tenant_id=row.tenant_id,
        name=row.name,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        path=row.path,
        metadata=row.metadata_json,
        created_at=row.created_at,
    )
