from sqlalchemy import String, JSON, Double, Integer, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class Dataset(Base):
    __tablename__ = 'datasets'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    source: Mapped[str] = mapped_column(String(200), default='manual.json')
    records: Mapped[list] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    # 大文件批次只保存行数，记录本身按行存放在 dataset_records，避免整份文件驻留内存。
    record_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Double)


class DatasetRecord(Base):
    """大文件批次的分行存储。seq 从 1 开始，与源文件记录顺序一致。"""
    __tablename__ = 'dataset_records'
    __table_args__ = (
        UniqueConstraint('dataset_id', 'seq', name='uq_dataset_record_position'),
        Index('ix_dataset_records_dataset', 'dataset_id'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    seq: Mapped[int] = mapped_column(Integer)
    sample: Mapped[str] = mapped_column(String(200))
    route: Mapped[str] = mapped_column(String(200))
    minute: Mapped[float] = mapped_column(Double)
    temperature: Mapped[float] = mapped_column(Double)
