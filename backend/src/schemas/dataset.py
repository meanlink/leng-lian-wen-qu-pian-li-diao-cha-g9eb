from pydantic import BaseModel, Field

class DatasetInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    source: str = Field(default='manual.json', max_length=200)
    records: list[dict] = Field(min_length=5, max_length=5000)

class IntakeCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    source: str = Field(default='records.csv', max_length=200)
    format: str = Field(default='csv', pattern='^(csv|CSV)$')
    total_bytes: int = Field(gt=0)
    chunk_size: int = Field(gt=0)
    total_chunks: int | None = Field(default=None, gt=0)
    checksum: str = Field(default='', max_length=64)

class IntakeSegmentMeta(BaseModel):
    sha256: str = Field(default='', max_length=64)
