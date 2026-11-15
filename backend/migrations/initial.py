from sqlalchemy import inspect, text
from src.config.database import Base, engine
from src import models


def _ensure_columns():
    """create_all 只建新表；已有库需补上新增列。"""
    inspector = inspect(engine)
    if 'datasets' not in inspector.get_table_names():
        return
    columns = {c['name'] for c in inspector.get_columns('datasets')}
    if 'record_count' not in columns:
        with engine.begin() as connection:
            connection.execute(text('ALTER TABLE datasets ADD COLUMN record_count INTEGER NULL'))


def migrate():
    Base.metadata.create_all(engine)
    _ensure_columns()


if __name__ == '__main__':
    migrate()
