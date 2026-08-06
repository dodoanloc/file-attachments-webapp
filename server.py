from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from statement_due import build as build_statement_due
from pathlib import Path
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
UPLOAD_DIR = DATA_DIR / 'uploads'
STATEMENT_ROOT = Path(os.getenv('STATEMENT_ROOT', str(DATA_DIR / 'statements')))
STATEMENT_TYPES = {'overdraft': 'Sao kê thấu chi', 'guarantee': 'Sao kê bảo lãnh'}
DB_PATH = DATA_DIR / 'attachments.db'
CCCD_AUTH_URL = os.getenv('CCCD_AUTH_URL', 'http://127.0.0.1:8010/api/auth/login')
MAX_FILE_SIZE = 500 * 1024 * 1024
RETENTION_DAYS = 7
CHUNK_SIZE = 1024 * 1024

app = FastAPI(title='Đính kèm file nội bộ')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/\x00-\x1f]+', '_', name or 'file')
    cleaned = re.sub(r'\s+', ' ', cleaned).strip().strip('.')
    return cleaned[:180] or 'file'


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    for statement_type in STATEMENT_TYPES:
        (STATEMENT_ROOT / statement_type).mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS attachments (
                id TEXT PRIMARY KEY,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                uploaded_by TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_attachments_uploaded_at ON attachments(uploaded_at DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_attachments_expires_at ON attachments(expires_at)')
        conn.execute(
            '''
            CREATE TABLE IF NOT EXISTS statement_uploads (
                id TEXT PRIMARY KEY,
                statement_type TEXT NOT NULL,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                path TEXT NOT NULL,
                size INTEGER NOT NULL,
                uploaded_at TEXT NOT NULL,
                uploaded_by TEXT NOT NULL
            )
            '''
        )
        conn.execute('CREATE INDEX IF NOT EXISTS idx_statement_uploads_type_uploaded ON statement_uploads(statement_type, uploaded_at DESC)')
        conn.commit()


def cleanup_expired() -> int:
    init_db()
    cutoff = iso(now_utc())
    removed = 0
    with get_conn() as conn:
        rows = conn.execute('SELECT id, stored_name FROM attachments WHERE expires_at <= ?', (cutoff,)).fetchall()
        for row in rows:
            path = UPLOAD_DIR / row['stored_name']
            try:
                if path.exists():
                    path.unlink()
            finally:
                conn.execute('DELETE FROM attachments WHERE id = ?', (row['id'],))
                removed += 1
        conn.commit()
    return removed


def verify_cccd_user(username: str, password: str) -> dict:
    username = (username or '').strip()
    if not username or not password:
        raise HTTPException(status_code=401, detail='Vui lòng đăng nhập')
    payload = json.dumps({'username': username, 'password': password}).encode('utf-8')
    req = urllib_request.Request(
        CCCD_AUTH_URL,
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib_request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(status_code=401, detail='Sai tài khoản hoặc mật khẩu') from exc
        raise HTTPException(status_code=502, detail=f'Lỗi xác thực user CCCD: HTTP {exc.code}') from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail='Không kết nối được hệ thống user CCCD') from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Lỗi xác thực user CCCD') from exc
    if not data.get('success'):
        raise HTTPException(status_code=401, detail='Sai tài khoản hoặc mật khẩu')
    return data.get('user') or {'username': username}


@app.on_event('startup')
def on_startup() -> None:
    init_db()
    cleanup_expired()


@app.post('/api/login')
def login(payload: dict):
    username = str(payload.get('username') or '')
    password = str(payload.get('password') or '')
    user = verify_cccd_user(username, password)
    return {'success': True, 'user': user}


@app.get('/api/files')
def list_files(username: str, password: str):
    user = verify_cccd_user(username, password)
    cleanup_expired()
    with get_conn() as conn:
        rows = conn.execute(
            'SELECT id, original_name, size, uploaded_at, uploaded_by, expires_at FROM attachments ORDER BY uploaded_at DESC'
        ).fetchall()
    return {'success': True, 'user': user, 'items': [dict(row) for row in rows], 'max_size': MAX_FILE_SIZE, 'retention_days': RETENTION_DAYS}


@app.post('/api/files')
async def upload_file(username: str = Form(...), password: str = Form(...), file: UploadFile = File(...)):
    user = verify_cccd_user(username, password)
    cleanup_expired()
    content_length = file.headers.get('content-length') if file.headers else None
    if content_length and int(content_length) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail='File vượt quá giới hạn 500 MB')

    attachment_id = uuid.uuid4().hex
    original_name = safe_filename(file.filename or 'file')
    stored_name = f'{attachment_id}_{original_name}'
    path = UPLOAD_DIR / stored_name
    size = 0
    try:
        with path.open('wb') as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    out.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail='File vượt quá giới hạn 500 MB')
                out.write(chunk)
    finally:
        await file.close()

    uploaded_at = now_utc()
    expires_at = uploaded_at + timedelta(days=RETENTION_DAYS)
    uploaded_by = user.get('username') or username
    with get_conn() as conn:
        conn.execute(
            '''
            INSERT INTO attachments (id, original_name, stored_name, size, uploaded_at, uploaded_by, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (attachment_id, original_name, stored_name, size, iso(uploaded_at), uploaded_by, iso(expires_at)),
        )
        conn.commit()
    return {'success': True, 'item': {'id': attachment_id, 'original_name': original_name, 'size': size, 'uploaded_at': iso(uploaded_at), 'uploaded_by': uploaded_by, 'expires_at': iso(expires_at)}}


@app.get('/api/statements')
def list_statements(username: str, password: str):
    user = verify_cccd_user(username, password)
    init_db()
    with get_conn() as conn:
        rows = conn.execute('SELECT * FROM statement_uploads ORDER BY uploaded_at DESC LIMIT 100').fetchall()
    return {'success': True, 'user': user, 'items': [dict(row) for row in rows], 'types': STATEMENT_TYPES}


@app.get('/api/statements/latest/{statement_type}')
def latest_statement(statement_type: str):
    if statement_type not in STATEMENT_TYPES:
        raise HTTPException(status_code=404, detail='Loại sao kê không hợp lệ')
    init_db()
    with get_conn() as conn:
        row = conn.execute('SELECT * FROM statement_uploads WHERE statement_type = ? ORDER BY uploaded_at DESC LIMIT 1', (statement_type,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Chưa có file sao kê')
    return {'success': True, 'item': dict(row)}


@app.get('/api/statements/due/{statement_type}')
def statement_due(statement_type: str, date: str = ''):
    if statement_type not in STATEMENT_TYPES:
        raise HTTPException(status_code=404, detail='Loại sao kê không hợp lệ')
    try:
        return build_statement_due(statement_type, date or None)
    except SystemExit as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Lỗi đọc sao kê: {exc}') from exc


@app.post('/api/statements/{statement_type}')
async def upload_statement(statement_type: str, username: str = Form(...), password: str = Form(...), file: UploadFile = File(...)):
    if statement_type not in STATEMENT_TYPES:
        raise HTTPException(status_code=404, detail='Loại sao kê không hợp lệ')
    user = verify_cccd_user(username, password)
    init_db()
    original_name = safe_filename(file.filename or 'statement')
    ext = Path(original_name).suffix.lower()
    if ext not in {'.xls', '.xlsx', '.csv'}:
        raise HTTPException(status_code=400, detail='Chỉ nhận file .xls, .xlsx, .csv')
    upload_id = uuid.uuid4().hex
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    stored_name = f'{stamp}_{upload_id}_{original_name}'
    path = STATEMENT_ROOT / statement_type / stored_name
    path.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    try:
        with path.open('wb') as out:
            while True:
                chunk = await file.read(CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    out.close()
                    path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail='File vượt quá giới hạn 500 MB')
                out.write(chunk)
    finally:
        await file.close()
    uploaded_at = now_utc()
    uploaded_by = user.get('username') or username
    with get_conn() as conn:
        conn.execute(
            '''
            INSERT INTO statement_uploads (id, statement_type, original_name, stored_name, path, size, uploaded_at, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (upload_id, statement_type, original_name, stored_name, str(path), size, iso(uploaded_at), uploaded_by),
        )
        conn.commit()
    return {'success': True, 'item': {'id': upload_id, 'statement_type': statement_type, 'original_name': original_name, 'stored_name': stored_name, 'path': str(path), 'size': size, 'uploaded_at': iso(uploaded_at), 'uploaded_by': uploaded_by}}


@app.get('/api/files/{attachment_id}/download')
def download_file(attachment_id: str, username: str, password: str):
    verify_cccd_user(username, password)
    cleanup_expired()
    with get_conn() as conn:
        row = conn.execute('SELECT original_name, stored_name FROM attachments WHERE id = ?', (attachment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='File không tồn tại hoặc đã hết hạn')
    path = UPLOAD_DIR / row['stored_name']
    if not path.exists():
        raise HTTPException(status_code=404, detail='File không tồn tại trên máy chủ')
    return FileResponse(path, filename=row['original_name'])


@app.delete('/api/files/{attachment_id}')
def delete_file(attachment_id: str, username: str, password: str):
    user = verify_cccd_user(username, password)
    with get_conn() as conn:
        row = conn.execute('SELECT stored_name, uploaded_by FROM attachments WHERE id = ?', (attachment_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='File không tồn tại')
        if row['uploaded_by'] != user.get('username') and user.get('role') != 'admin':
            raise HTTPException(status_code=403, detail='Chỉ người tải lên hoặc admin được xoá')
        path = UPLOAD_DIR / row['stored_name']
        if path.exists():
            path.unlink()
        conn.execute('DELETE FROM attachments WHERE id = ?', (attachment_id,))
        conn.commit()
    return {'success': True}


app.mount('/', StaticFiles(directory=BASE_DIR / 'public', html=True), name='static')


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8894)
