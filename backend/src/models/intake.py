from sqlalchemy import BigInteger, Double, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class IntakeSession(Base):
    """A resumable, chunked file intake. The receipt is the resume token."""

    __tablename__ = 'intake_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(200), default='records.csv')
    format: Mapped[str] = mapped_column(String(16), default='csv')
    total_bytes: Mapped[int] = mapped_column(BigInteger)
    chunk_size: Mapped[int] = mapped_column(Integer)
    total_chunks: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64), default='')
    content_checksum: Mapped[str] = mapped_column(String(64), default='')
    status: Mapped[str] = mapped_column(String(20), default='receiving')
    failure_reason: Mapped[str] = mapped_column(String(1000), default='')
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    updated_at: Mapped[float] = mapped_column(Double)


class IntakeSegment(Base):
    """A confirmed chunk. Unique(session, index) makes re-uploads idempotent."""

    __tablename__ = 'intake_segments'
    __table_args__ = (UniqueConstraint('session_id', 'segment_index', name='uq_intake_segment_position'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('intake_sessions.id'))
    segment_index: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Double)
