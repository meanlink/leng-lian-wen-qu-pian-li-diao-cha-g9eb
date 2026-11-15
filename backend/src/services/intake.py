"""分段接收、断点续传与整份校验后发布。

设计要点：
- 每个分段的原文按位置落盘，数据库只记录确认过的位置与指纹；
- 同位置同内容重复上传视为幂等，同位置异内容直接拒绝覆盖；
- 校验只做流式读取，发布按批写库，不把整份文件读入内存；
- 只有全部分段齐全、整份指纹一致、逐行字段合法后才创建批次；
- 发布使用接收单行锁与批次名唯一约束，重试或并发完成都只关联一个批次。
"""
import csv
import fcntl
import hashlib
import math
import os
import re
import secrets
import time
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import ARTIFACT_DIR
from src.models import Dataset, DatasetRecord, IntakeSegment, IntakeSession
from src.services.business import metadata

MAX_BYTES = 256 * 1024 * 1024
MAX_RECORDS = 1_000_000
MAX_SEGMENTS = 10000
INSERT_BATCH = 1000
TEXT_LIMIT = 200
SHA256_RE = re.compile(r'^[0-9a-f]{64}$')

csv.field_size_limit(4 * 1024 * 1024)


class IntakeValidation(Exception):
    """可恢复的校验失败：保留接收现场，不发布批次。"""

    def __init__(self, message, status_code=422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _required_keys():
    return [c['key'] for c in metadata()['columns']]


def _numeric_keys():
    return {c['key'] for c in metadata()['columns'] if c['type'] == 'number'}


def _label(key):
    return next((c['label'] for c in metadata()['columns'] if c['key'] == key), key)


def _root(receipt):
    return Path(ARTIFACT_DIR) / 'intake' / receipt


def _segment_file(receipt, index, sha256):
    # 文件名包含内容指纹：并发上传同一位置时不同内容不会互相覆盖，
    # 数据库中的唯一行才是最终确认归属。
    return _root(receipt) / f'{index:06d}-{sha256}.seg'


def _index_files(receipt, index):
    return list(_root(receipt).glob(f'{index:06d}-*.seg'))


def _expected_size(session, index):
    if index < 0 or index >= session.total_chunks:
        return 0
    if index == session.total_chunks - 1:
        return session.total_bytes - session.chunk_size * (session.total_chunks - 1)
    return session.chunk_size


def _short_list(values, limit=20):
    values = list(values)
    shown = '、'.join(str(v) for v in values[:limit])
    return shown + (' 等 %d 段' % len(values) if len(values) > limit else '')


def _write_atomic(target, payload):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + '.tmp-' + uuid.uuid4().hex)
    with open(temp, 'wb') as handle:
        handle.write(payload)
    os.replace(temp, target)


def state(db, session):
    rows = list(db.execute(
        select(IntakeSegment.segment_index, IntakeSegment.size)
        .where(IntakeSegment.session_id == session.id)
        .order_by(IntakeSegment.segment_index)
    ))
    present = {index for index, _ in rows}
    confirmed_bytes = sum(size for _, size in rows)
    missing = [i for i in range(session.total_chunks) if i not in present]
    prefix = 0
    prefix_bytes = 0
    while prefix < session.total_chunks and prefix in present:
        prefix_bytes += _expected_size(session, prefix)
        prefix += 1
    return {
        'receipt': session.receipt,
        'name': session.name,
        'source': session.source,
        'format': session.format,
        'total_bytes': session.total_bytes,
        'chunk_size': session.chunk_size,
        'total_chunks': session.total_chunks,
        'checksum': session.checksum,
        'content_checksum': session.content_checksum,
        'status': session.status,
        'failure_reason': session.failure_reason,
        'dataset_id': session.dataset_id,
        'record_count': session.record_count,
        'confirmed_segments': sorted(present),
        'confirmed_count': len(present),
        'confirmed_bytes': confirmed_bytes,
        'confirmed_prefix': prefix,
        'confirmed_prefix_bytes': prefix_bytes,
        'missing_segments': missing,
        'missing_count': len(missing),
        'can_finalize': session.status != 'published' and not missing,
        'created_at': session.created_at,
        'updated_at': session.updated_at,
    }


def recent(db, limit=20):
    sessions = list(db.scalars(
        select(IntakeSession).order_by(IntakeSession.updated_at.desc()).limit(limit)))
    return [state(db, session) for session in sessions]


def create(db, data, user):
    name = data.name.strip()
    if len(name) < 2:
        raise HTTPException(422, '批次名称去除空格后至少需要两个字符')
    if data.format != 'csv':
        raise HTTPException(422, '分段接收当前只支持 CSV；小批次 JSON 仍走原有接入方式')
    if data.total_bytes <= 0:
        raise HTTPException(422, '文件必须包含至少一个字节')
    if data.total_bytes > MAX_BYTES:
        raise HTTPException(422, f'单个文件上限 {MAX_BYTES // 1024 // 1024} MB，请拆分后接入')
    if data.chunk_size <= 0:
        raise HTTPException(422, '分段大小必须为正整数字节')
    total_chunks = math.ceil(data.total_bytes / data.chunk_size)
    if total_chunks > MAX_SEGMENTS:
        raise HTTPException(422, f'分段数上限 {MAX_SEGMENTS}，请增大分段大小')
    if data.total_chunks is not None and data.total_chunks != total_chunks:
        raise HTTPException(
            422, f'分段数与文件大小不一致：按 {data.chunk_size} 字节应为 {total_chunks} 段')
    expected = (data.checksum or '').strip().lower()
    if expected and not SHA256_RE.match(expected):
        raise HTTPException(422, '整份校验指纹须为 64 位十六进制 SHA-256')
    if db.scalar(select(Dataset).where(Dataset.name == name)):
        raise HTTPException(409, '该数据批次名已存在，请修改名称')
    now = time.time()
    session = IntakeSession(
        receipt='R-' + secrets.token_hex(16), name=name,
        source=(data.source or 'records.csv')[:200], format='csv',
        total_bytes=data.total_bytes, chunk_size=data.chunk_size,
        total_chunks=total_chunks, checksum=expected, content_checksum='',
        status='receiving', failure_reason='', dataset_id=None, record_count=None,
        created_by=user.id, created_at=now, updated_at=now,
    )
    db.add(session)
    db.flush()
    return session


def expected_size(db, receipt, index):
    return _expected_size(_load(db, receipt), index)


def _load(db, receipt):
    session = db.scalar(select(IntakeSession).where(IntakeSession.receipt == receipt))
    if not session:
        raise HTTPException(404, '接收单不存在，请核对接收单号码')
    return session


def _existing_segments(db, session):
    return {index: sha for index, sha in db.execute(
        select(IntakeSegment.segment_index, IntakeSegment.sha256)
        .where(IntakeSegment.session_id == session.id)
    )}


def store_segment(db, receipt, index, payload, declared_sha=None):
    session = _load(db, receipt)
    if session.status == 'published':
        raise HTTPException(409, '该接收单已发布批次，不能再补传分段')
    if index < 0 or index >= session.total_chunks:
        raise HTTPException(422, f'分段位置应在 0–{session.total_chunks - 1} 之间')
    expected = _expected_size(session, index)
    if len(payload) != expected:
        raise HTTPException(
            422, f'第 {index} 段应为 {expected} 字节，实际 {len(payload)} 字节')
    digest = hashlib.sha256(payload).hexdigest()
    declared = (declared_sha or '').strip().lower()
    if declared and declared != digest:
        raise HTTPException(422, f'第 {index} 段内容与声明指纹不符，请重新读取源文件后上传')

    existing = _existing_segments(db, session)
    duplicate = index in existing
    if duplicate and existing[index] != digest:
        raise HTTPException(
            409, f'第 {index} 段已确认不同内容，拒绝覆盖；如需更换请重新创建接收单')

    target = _segment_file(session.receipt, index, digest)
    if not duplicate:
        try:
            _write_atomic(target, payload)
            db.add(IntakeSegment(
                session_id=session.id, segment_index=index, size=len(payload),
                sha256=digest, created_at=time.time()))
            db.flush()
        except IntegrityError:
            # 同一位置被并发写入：以先落库的内容为准，再按幂等或异值规则答复。
            db.rollback()
            session = _load(db, receipt)
            existing = _existing_segments(db, session)
            if index not in existing:
                raise HTTPException(409, '该分段位置正在被其他请求接收，请稍后重试')
            duplicate = True
            if existing[index] != digest:
                target.unlink(missing_ok=True)
                raise HTTPException(
                    409, f'第 {index} 段已确认不同内容，拒绝覆盖；如需更换请重新创建接收单')
    elif not target.exists():
        _write_atomic(target, payload)
    # 只保留该位置已确认归属的内容文件，避免历史尝试残留占用磁盘。
    keep = _segment_file(session.receipt, index, (existing.get(index) or digest))
    for stale in _index_files(session.receipt, index):
        if stale != keep:
            stale.unlink(missing_ok=True)

    if session.status == 'failed':
        session.status = 'receiving'
        session.failure_reason = ''
    session.updated_at = time.time()
    db.commit()
    return {**state(db, _load(db, receipt)), 'accepted': not duplicate, 'duplicate': duplicate}


def _assemble(session, confirmed):
    root = _root(session.receipt)
    target = root / 'assembled.csv'
    temp = root / ('assembled.tmp-' + uuid.uuid4().hex)
    digest = hashlib.sha256()
    try:
        with open(temp, 'wb') as out:
            for index in range(session.total_chunks):
                if index not in confirmed:
                    raise IntakeValidation(
                        f'第 {index} 段尚未确认，接收现场已保留；请补传该分段', 409)
                part = _segment_file(session.receipt, index, confirmed[index])
                if not part.exists():
                    raise IntakeValidation(
                        f'第 {index} 段文件缺失，接收现场已保留；请补传该分段', 409)
                expected = _expected_size(session, index)
                if part.stat().st_size != expected:
                    raise IntakeValidation(
                        f'第 {index} 段文件大小异常，接收现场已保留；请重新补传该分段', 409)
                with open(part, 'rb') as handle:
                    while True:
                        block = handle.read(1024 * 1024)
                        if not block:
                            break
                        digest.update(block)
                        out.write(block)
        if temp.stat().st_size != session.total_bytes:
            raise IntakeValidation('分段合计大小与接收单不一致，接收现场已保留', 409)
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _iter_records(path):
    """流式解析；跨段切开的中文、引号逗号、跨行字段由拼接后的 CSV 一次还原。"""
    numeric = _numeric_keys()
    required = _required_keys()
    with open(path, newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            raise IntakeValidation('CSV 为空，缺少表头')
        except csv.Error as error:
            raise IntakeValidation(f'CSV 表头格式错误：{error}')
        except UnicodeDecodeError:
            raise IntakeValidation('文件包含非 UTF-8 字符，请转为 UTF-8 后重新接收')
        keys = [h.strip() for h in header]
        if not keys or any(not k for k in keys):
            raise IntakeValidation('CSV 表头存在空字段名')
        duplicated = {k for k in required if keys.count(k) > 1}
        if duplicated:
            raise IntakeValidation(
                'CSV 表头重复：' + '、'.join(_label(k) for k in sorted(duplicated)))
        missing = [k for k in required if k not in keys]
        if missing:
            raise IntakeValidation('CSV 缺少字段：' + '、'.join(_label(k) for k in missing))
        positions = {key: keys.index(key) for key in required}
        row_no = 1
        try:
            for row in reader:
                row_no += 1
                if not row or all(cell == '' for cell in row):
                    continue
                if len(row) != len(keys):
                    raise IntakeValidation(
                        f'第 {row_no} 行字段数 {len(row)} 与表头 {len(keys)} 不一致')
                values = {}
                for key in required:
                    value = row[positions[key]]
                    if value == '':
                        raise IntakeValidation(f'第 {row_no} 行缺少字段 {_label(key)}')
                    if key in numeric:
                        try:
                            number = float(value)
                        except (TypeError, ValueError, OverflowError):
                            raise IntakeValidation(
                                f'第 {row_no} 行 {_label(key)} 必须为有限数值')
                        if not math.isfinite(number):
                            raise IntakeValidation(
                                f'第 {row_no} 行 {_label(key)} 必须为有限数值')
                        values[key] = number
                    else:
                        if len(value) > TEXT_LIMIT:
                            raise IntakeValidation(
                                f'第 {row_no} 行 {_label(key)} 超过 {TEXT_LIMIT} 个字符')
                        values[key] = value
                yield values
        except csv.Error as error:
            raise IntakeValidation(f'第 {row_no} 行 CSV 格式错误：{error}')
        except UnicodeDecodeError:
            raise IntakeValidation('文件包含非 UTF-8 字符，请转为 UTF-8 后重新接收')


def _validate(path):
    count = 0
    for _ in _iter_records(path):
        count += 1
        if count > MAX_RECORDS:
            raise IntakeValidation(f'记录数超过上限 {MAX_RECORDS} 条')
    if count == 0:
        raise IntakeValidation('文件没有有效测温记录')
    return count


def _fail(db, session, message, status_code):
    db.rollback()
    session = _load(db, session.receipt)
    session.status = 'failed'
    session.failure_reason = message[:990]
    session.updated_at = time.time()
    db.commit()
    raise HTTPException(status_code, message)


def _publish(db, session, path, count, content_checksum):
    dataset = Dataset(
        name=session.name, source=session.source, records=[],
        checksum=content_checksum, record_count=count, created_at=time.time())
    db.add(dataset)
    db.flush()
    batch = []
    seq = 0
    for values in _iter_records(path):
        seq += 1
        batch.append({
            'dataset_id': dataset.id, 'seq': seq,
            'sample': values['sample'], 'route': values['route'],
            'minute': values['minute'], 'temperature': values['temperature'],
        })
        if len(batch) >= INSERT_BATCH:
            db.execute(insert(DatasetRecord), batch)
            batch = []
    if batch:
        db.execute(insert(DatasetRecord), batch)
    if seq != count:
        raise IntakeValidation('校验与发布读取的记录数不一致，请重新核对源文件')
    session.status = 'published'
    session.dataset_id = dataset.id
    session.record_count = count
    session.content_checksum = content_checksum
    session.failure_reason = ''
    session.updated_at = time.time()
    db.commit()
    return dataset


def _dataset_dict(dataset):
    if not dataset:
        return None
    rows = dataset.record_count if dataset.record_count is not None else len(dataset.records)
    return {
        'id': dataset.id, 'name': dataset.name, 'source': dataset.source,
        'rows': rows, 'checksum': dataset.checksum, 'created_at': dataset.created_at,
    }


def _cleanup(receipt):
    root = _root(receipt)
    if not root.exists():
        return
    for part in root.glob('*.seg'):
        part.unlink(missing_ok=True)
    (root / 'assembled.csv').unlink(missing_ok=True)
    for temp in root.glob('assembled.tmp-*'):
        temp.unlink(missing_ok=True)


def finalize(db, receipt):
    _load(db, receipt)
    root = _root(receipt)
    root.mkdir(parents=True, exist_ok=True)
    with open(root / '.finalize.lock', 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            return _finalize_locked(db, receipt)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _finalize_locked(db, receipt):
    db.expire_all()
    # 行锁 + 文件锁：并发完成时只有第一个请求进入发布，后续请求读到已发布状态。
    session = db.scalar(
        select(IntakeSession).where(IntakeSession.receipt == receipt).with_for_update())
    if not session:
        raise HTTPException(404, '接收单不存在，请核对接收单号码')
    if session.status == 'published' and session.dataset_id:
        dataset = db.get(Dataset, session.dataset_id)
        db.commit()
        return {**state(db, session), 'dataset': _dataset_dict(dataset), 'published': True}

    present = {index: sha for index, sha in db.execute(
        select(IntakeSegment.segment_index, IntakeSegment.sha256).where(
            IntakeSegment.session_id == session.id))}
    missing = [i for i in range(session.total_chunks) if i not in present]
    if missing:
        _fail(db, session,
              f'缺少分段：{_short_list(missing)}（已确认 {len(present)}/{session.total_chunks} 段）', 409)

    try:
        content_checksum = _assemble(session, present)
    except IntakeValidation as error:
        _fail(db, session, error.message, error.status_code)
    except OSError:
        _fail(db, session, '接收现场文件不可读，请检查存储后重试；已确认分段仍会保留', 500)

    if session.checksum and content_checksum != session.checksum:
        _fail(db, session,
              f'整份校验不符：接收单声明 {session.checksum}，实际 {content_checksum}；'
              '接收现场已保留，请核对源文件后重新接收', 422)

    path = _root(session.receipt) / 'assembled.csv'
    try:
        count = _validate(path)
    except IntakeValidation as error:
        _fail(db, session, error.message, error.status_code)
    except Exception:
        _fail(db, session, '文件解析失败，接收现场已保留，请核对 CSV 格式', 422)

    try:
        dataset = _publish(db, session, path, count, content_checksum)
    except IntakeValidation as error:
        _fail(db, session, error.message, error.status_code)
    except IntegrityError:
        db.rollback()
        session = _load(db, receipt)
        if session.status == 'published' and session.dataset_id:
            dataset = db.get(Dataset, session.dataset_id)
            return {**state(db, session), 'dataset': _dataset_dict(dataset), 'published': True}
        _fail(db, session,
              f'批次名「{session.name}」已被其他批次占用，请修改名称后重新创建接收单', 409)
    except Exception:
        _fail(db, session, '批次写入失败，接收现场已保留，请稍后重试', 500)

    _cleanup(session.receipt)
    db.expire_all()
    session = _load(db, receipt)
    return {**state(db, session), 'dataset': _dataset_dict(dataset), 'published': True}
