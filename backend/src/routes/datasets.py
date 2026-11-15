from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from src.config.database import get_db
from src.models import Dataset, DatasetRecord, IntakeSession
from src.schemas.dataset import DatasetInput, IntakeCreate
from src.middlewares.auth import current_user, can_write
from src.services import datasets, intake
from src.services.audit import record
from src.utils.serialization import dataset_dict
router = APIRouter()


@router.get('/datasets')
def listing(db=Depends(get_db), u=Depends(current_user)):
    return [dataset_dict(d) for d in db.scalars(select(Dataset).order_by(Dataset.id.desc()))]


@router.post('/datasets', status_code=201)
def create(data: DatasetInput, db=Depends(get_db), u=Depends(can_write)):
    d = datasets.create(db, data)
    record(db, u.email, 'dataset.create', {'id': d.id, 'rows': len(d.records)})
    db.commit()
    return dataset_dict(d)


@router.get('/datasets/{id}')
def detail(id: int, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=500),
           db=Depends(get_db), u=Depends(current_user)):
    d = db.get(Dataset, id)
    if not d:
        raise HTTPException(404, '数据批次不存在')
    if d.record_count is None:
        total = len(d.records)
        return {**dataset_dict(d), 'records': d.records[offset:offset + limit],
                'offset': offset, 'limit': limit, 'total': total}
    rows = db.scalars(select(DatasetRecord).where(DatasetRecord.dataset_id == d.id)
                      .order_by(DatasetRecord.seq).offset(offset).limit(limit))
    columns = ['sample', 'route', 'minute', 'temperature']
    return {**dataset_dict(d), 'records': [{k: getattr(r, k) for k in columns} for r in rows],
            'offset': offset, 'limit': limit, 'total': d.record_count}


# ---- 分段接收：创建接收单 / 查看进度 / 补传分段 / 完成校验发布 ----

@router.post('/intakes', status_code=201)
def intake_create(data: IntakeCreate, db=Depends(get_db), u=Depends(can_write)):
    session = intake.create(db, data, u)
    record(db, u.email, 'dataset.intake.create',
           {'receipt': session.receipt, 'bytes': session.total_bytes,
            'chunks': session.total_chunks})
    db.commit()
    return intake.state(db, session)


@router.get('/intakes')
def intake_list(db=Depends(get_db), u=Depends(current_user)):
    return intake.recent(db)


@router.get('/intakes/{receipt}')
def intake_detail(receipt: str, db=Depends(get_db), u=Depends(current_user)):
    session = db.scalar(select(IntakeSession).where(IntakeSession.receipt == receipt))
    if not session:
        raise HTTPException(404, '接收单不存在，请核对接收单号码')
    return intake.state(db, session)


@router.put('/intakes/{receipt}/segments/{index}')
async def intake_segment(receipt: str, index: int, request: Request,
                         x_segment_sha256: str = Header(default=''),
                         db=Depends(get_db), u=Depends(can_write)):
    # 分段字节直接作为请求体，先按预期长度设上限，避免异常大包占用内存。
    expected = intake.expected_size(db, receipt, index)
    declared_length = request.headers.get('content-length')
    if expected and declared_length and declared_length.isdigit() and int(declared_length) > expected:
        raise HTTPException(413, f'第 {index} 段应不超过 {expected} 字节')
    payload = await request.body()
    if expected and len(payload) > expected:
        raise HTTPException(422, f'第 {index} 段应不超过 {expected} 字节')
    result = intake.store_segment(db, receipt, index, payload, x_segment_sha256)
    if result['duplicate']:
        record(db, u.email, 'dataset.intake.duplicate', {'receipt': receipt, 'segment': index})
    else:
        record(db, u.email, 'dataset.intake.segment',
               {'receipt': receipt, 'segment': index, 'bytes': len(payload),
                'confirmed': result['confirmed_count']})
    db.commit()
    return result


@router.post('/intakes/{receipt}/finalize')
def intake_finalize(receipt: str, db=Depends(get_db), u=Depends(can_write)):
    try:
        result = intake.finalize(db, receipt)
    except HTTPException as error:
        # 失败也留痕：接收单、原因与已确认状态可从审计与详情页复核。
        record(db, u.email, 'dataset.intake.failed',
               {'receipt': receipt, 'reason': str(error.detail)[:500]})
        db.commit()
        raise
    dataset = result.get('dataset') or {}
    record(db, u.email, 'dataset.intake.published',
           {'receipt': receipt, 'dataset': dataset.get('id'), 'rows': dataset.get('rows')})
    db.commit()
    return result
