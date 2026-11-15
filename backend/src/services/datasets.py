import math, time
from fastapi import HTTPException
from sqlalchemy import select
from src.models import Dataset, DatasetRecord
from src.services.business import metadata
from src.utils.serialization import checksum


def create(db, data):
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '批次名称去除空格后至少需要两个字符')
    if db.scalar(select(Dataset).where(Dataset.name == name)):
        raise HTTPException(409, '该数据批次名已存在，请修改名称')
    for index, row in enumerate(data.records):
        for c in metadata()['columns']:
            value = row.get(c['key'])
            if value is None or value == '':
                raise HTTPException(422, f'第 {index + 1} 行缺少字段 {c["label"]}')
            if c['type'] == 'number':
                try:
                    if isinstance(value, bool) or not math.isfinite(float(value)):
                        raise ValueError()
                    row[c['key']] = float(value)
                except (TypeError, ValueError, OverflowError):
                    raise HTTPException(422, f'第 {index + 1} 行 {c["label"]} 必须为有限数值')
    d = Dataset(name=name, source=data.source, records=data.records,
                checksum=checksum(data.records), record_count=None, created_at=time.time())
    db.add(d); db.flush()
    return d


def row_count(dataset):
    return dataset.record_count if dataset.record_count is not None else len(dataset.records)


def iter_records(db, dataset):
    """统一的小批次内联记录与大文件分行记录的读取入口，大文件按批流式取出。"""
    if dataset.record_count is None:
        for row in dataset.records:
            yield row
        return
    result = db.execute(
        select(DatasetRecord.sample, DatasetRecord.route,
               DatasetRecord.minute, DatasetRecord.temperature)
        .where(DatasetRecord.dataset_id == dataset.id)
        .order_by(DatasetRecord.seq)
        .execution_options(yield_per=2000))
    for sample, route, minute, temperature in result:
        yield {'sample': sample, 'route': route,
               'minute': minute, 'temperature': temperature}
    result.close()


def sample_records(db, dataset, limit=30, offset=0):
    if dataset.record_count is None:
        return dataset.records[offset:offset + limit]
    result = db.execute(
        select(DatasetRecord.sample, DatasetRecord.route,
               DatasetRecord.minute, DatasetRecord.temperature)
        .where(DatasetRecord.dataset_id == dataset.id)
        .order_by(DatasetRecord.seq)
        .offset(offset).limit(limit))
    return [{'sample': s, 'route': r, 'minute': m, 'temperature': t}
            for s, r, m, t in result]
