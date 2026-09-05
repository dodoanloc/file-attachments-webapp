from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import sqlite3
import uuid
import pyzipper
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
MIS_SHARE = '//10.0.43.21/mis'
MIS_REPORT_PATH = 'ChiNhanh/cn3511/MSSR08/3511'
MIS_EXTENSIONS = {'.zip'}
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


def validate_mis_report_name(name: str) -> str:
    name = str(name or '').strip().replace('\\', '/')
    if not re.fullmatch(r'\d{8}/[^/]+\.zip', name, flags=re.IGNORECASE):
        raise HTTPException(status_code=400, detail='Chỉ cho phép file .zip trong thư mục ngày yyyymmdd')
    return name


def _parse_smb_rows(output: str) -> list[dict]:
    rows: list[dict] = []
    pattern = re.compile(r'^\s*(?P<name>.+?)\s{2,}[A-Z]\s+(?P<size>\d+)\s+(?P<stamp>.+?)\s*$', re.MULTILINE)
    for match in pattern.finditer(output or ''):
        name = match.group('name').strip()
        try:
            stamp = datetime.strptime(match.group('stamp').strip(), '%a %b %d %H:%M:%S %Y')
        except ValueError:
            stamp = datetime.min
        rows.append({'name': name, 'kind': match.group(0).split()[1] if False else '', 'size': int(match.group('size')), 'modified_at': stamp.isoformat() if stamp != datetime.min else '', 'raw': match.group(0)})
    return rows


def latest_mis_date_folder(output: str) -> str:
    candidates: list[tuple[datetime, str]] = []
    pattern = re.compile(r'^\s*(?P<name>\d{8})\s{2,}D\s+\d+\s+(?P<stamp>.+?)\s*$', re.MULTILINE)
    for match in pattern.finditer(output or ''):
        name = match.group('name')
        try:
            datetime.strptime(name, '%Y%m%d')
            modified_at = datetime.strptime(match.group('stamp').strip(), '%a %b %d %H:%M:%S %Y')
            candidates.append((modified_at, name))
        except ValueError:
            continue
    if not candidates:
        raise HTTPException(status_code=404, detail='Không tìm thấy thư mục báo cáo ngày yyyymmdd trong MIS')
    return max(candidates, key=lambda item: (item[0], item[1]))[1]


def parse_mis_listing(output: str, folder: str = '') -> list[dict]:
    rows: list[dict] = []
    for raw_line in (output or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if '|' in line:
            fields = [part.strip() for part in line.split('|')]
            file_index = next((i for i, value in enumerate(fields) if Path(value).suffix.lower() == '.zip'), None)
            if file_index is None:
                continue
            name = fields[file_index]
            size = next((int(value) for value in fields if value.isdigit()), 0)
            stamp_text = ' '.join(value for value in fields if value not in {name, 'A', 'D'} and not value.isdigit()).strip()
        else:
            match = re.match(r'^\s*(?P<name>.+?\.zip)\s+[A-Z]\s+(?P<size>\d+)\s*(?P<stamp>.*?)\s*$', line, re.IGNORECASE)
            if not match:
                continue
            name = match.group('name').strip()
            size = int(match.group('size'))
            stamp_text = match.group('stamp').strip()
        if Path(name).suffix.lower() != '.zip' or Path(name).name != name:
            continue
        try:
            stamp = datetime.strptime(stamp_text, '%a %b %d %H:%M:%S %Y')
        except ValueError:
            stamp = datetime.min
        rows.append({'name': f'{folder}/{name}' if folder else name, 'display_name': name, 'size': size, 'modified_at': stamp.isoformat() if stamp != datetime.min else ''})
    return sorted(rows, key=lambda item: (item['modified_at'], item['name']), reverse=True)


def extract_csv_attachment(archive: Path, password: str, output_dir: Path) -> Path:
    """Return original ZIP when no extraction password was supplied; otherwise extract one CSV."""
    if not password:
        return archive
    try:
        with pyzipper.AESZipFile(archive) as zipped:
            candidates = [entry for entry in zipped.infolist() if not entry.is_dir() and Path(entry.filename).suffix.lower() == '.csv']
            if not candidates:
                raise HTTPException(status_code=422, detail='ZIP không có file CSV để đính kèm')
            if len(candidates) > 1:
                raise HTTPException(status_code=422, detail='ZIP có nhiều file CSV; chỉ hỗ trợ một file CSV')
            entry = candidates[0]
            member = Path(entry.filename)
            if member.is_absolute() or '..' in member.parts or entry.file_size > MAX_FILE_SIZE:
                raise HTTPException(status_code=422, detail='File CSV trong ZIP không hợp lệ hoặc vượt quá 500 MB')
            output_dir.mkdir(parents=True, exist_ok=True)
            target = output_dir / safe_filename(member.name)
            with zipped.open(entry, pwd=password.encode('utf-8')) as source, target.open('xb') as output:
                copied = 0
                while chunk := source.read(CHUNK_SIZE):
                    copied += len(chunk)
                    if copied > MAX_FILE_SIZE:
                        raise HTTPException(status_code=413, detail='File CSV giải nén vượt quá 500 MB')
                    output.write(chunk)
        archive.unlink(missing_ok=True)
        return target
    except HTTPException:
        raise
    except (RuntimeError, OSError, pyzipper.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail='Không thể giải nén ZIP. Kiểm tra mật khẩu giải nén.') from exc


def normalize_ad_credentials(username: str) -> tuple[str, str]:
    raw = str(username or '').strip()
    default_domain = 'corp.agribank.com.vn'
    if '\\' in raw:
        domain, account = raw.split('\\', 1)
        return account.strip(), domain.strip() or default_domain
    if '@' in raw:
        account, domain = raw.rsplit('@', 1)
        return account.strip(), domain.strip() or default_domain
    return raw, default_domain


def run_mis_smb(*, username: str, password: str, command: str) -> str:
    account, domain = normalize_ad_credentials(username)
    if not account or not password:
        raise HTTPException(status_code=400, detail='Nhập user và mật khẩu AD')
    auth_path: Path | None = None
    try:
        fd, raw_path = tempfile.mkstemp(prefix='cvi-', suffix='.smb-auth')
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(f'username={account}\ndomain={domain}\npassword={password}\n')
        auth_path = Path(raw_path)
        smb_command = f'cd "{MIS_REPORT_PATH}"; {command}'
        result = subprocess.run(
            ['smbclient', MIS_SHARE, '-g', '-A', str(auth_path), '-c', smb_command],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if result.returncode:
            raise HTTPException(status_code=502, detail='Không truy cập được thư mục MIS. Kiểm tra user/mật khẩu AD hoặc kết nối mạng.')
        return result.stdout
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail='Kết nối SMB MIS quá thời gian chờ') from exc
    finally:
        if auth_path:
            auth_path.unlink(missing_ok=True)


def mis_date_folders_newest_first(output: str) -> list[str]:
    candidates: list[tuple[datetime, str]] = []
    for raw_line in (output or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if '|' in line:
            fields = [part.strip() for part in line.split('|')]
            folder_index = next((i for i, value in enumerate(fields) if re.fullmatch(r'\d{8}', value)), None)
            if folder_index is None or 'D' not in fields:
                continue
            name = fields[folder_index]
            stamp_text = ' '.join(value for value in fields if value not in {name, 'D'} and not value.isdigit()).strip()
        else:
            match = re.match(r'^\s*(?P<name>\d{8})\s+D\s+\d+\s*(?P<stamp>.*?)\s*$', line)
            if not match:
                continue
            name = match.group('name')
            stamp_text = match.group('stamp').strip()
        try:
            datetime.strptime(name, '%Y%m%d')
        except ValueError:
            continue
        try:
            modified_at = datetime.strptime(stamp_text, '%a %b %d %H:%M:%S %Y')
        except ValueError:
            # Folder names carry the report date; retain valid folders even if
            # the SMB server uses a locale-specific timestamp format.
            modified_at = datetime.min
        candidates.append((modified_at, name))
    return [name for _, name in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)]


def list_mis_reports(*, username: str, password: str) -> list[dict]:
    root_listing = run_mis_smb(username=username, password=password, command='ls')
    folders = mis_date_folders_newest_first(root_listing)
    if not folders:
        raise HTTPException(status_code=404, detail='Không tìm thấy thư mục báo cáo ngày yyyymmdd trong MIS')
    for folder in folders:
        folder_listing = run_mis_smb(username=username, password=password, command=f'cd "{folder}"; ls')
        reports = parse_mis_listing(folder_listing, folder=folder)
        if reports:
            return reports
    raise HTTPException(status_code=404, detail='Không có file .zip trong các thư mục báo cáo ngày của MIS')


def copy_mis_report(*, username: str, password: str, report_name: str, uploaded_by: str, archive_password: str = '') -> dict:
    report_name = validate_mis_report_name(report_name)
    cleanup_expired()
    attachment_id = uuid.uuid4().hex
    original_name = safe_filename(Path(report_name).name)
    stored_name = f'{attachment_id}_{original_name}'
    destination = UPLOAD_DIR / stored_name
    try:
        run_mis_smb(username=username, password=password, command=f'get "{report_name}" "{destination}"')
        if not destination.exists() or destination.stat().st_size == 0:
            raise HTTPException(status_code=502, detail='Không thể sao chép file báo cáo từ MIS')
        extracted = extract_csv_attachment(destination, archive_password, UPLOAD_DIR)
        if extracted != destination:
            final_name = safe_filename(extracted.name)
            final_path = UPLOAD_DIR / f'{attachment_id}_{final_name}'
            extracted.replace(final_path)
            destination = final_path
            original_name = final_name
            # Download resolves from attachments.stored_name. Keep it aligned
            # with extracted CSV, not removed source ZIP.
            stored_name = final_path.name
        size = destination.stat().st_size
        uploaded_at = now_utc(); expires_at = uploaded_at + timedelta(days=RETENTION_DAYS)
        item = {'id': attachment_id, 'original_name': original_name, 'size': size, 'uploaded_at': iso(uploaded_at), 'uploaded_by': uploaded_by, 'expires_at': iso(expires_at)}
        with get_conn() as conn:
            conn.execute('INSERT INTO attachments (id, original_name, stored_name, size, uploaded_at, uploaded_by, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (attachment_id, original_name, stored_name, size, item['uploaded_at'], uploaded_by, item['expires_at']))
            conn.commit()
        return item
    except Exception:
        destination.unlink(missing_ok=True)
        raise


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


@app.post('/api/mis/reports')
def get_mis_reports(payload: dict):
    verify_cccd_user(str(payload.get('app_username') or ''), str(payload.get('app_password') or ''))
    items = list_mis_reports(username=str(payload.get('ad_username') or ''), password=str(payload.get('ad_password') or ''))
    return {'success': True, 'items': items}


@app.post('/api/mis/attach')
def attach_mis_report(payload: dict):
    user = verify_cccd_user(str(payload.get('app_username') or ''), str(payload.get('app_password') or ''))
    item = copy_mis_report(username=str(payload.get('ad_username') or ''), password=str(payload.get('ad_password') or ''), report_name=str(payload.get('report_name') or ''), uploaded_by=user.get('username') or str(payload.get('app_username') or ''), archive_password=str(payload.get('archive_password') or ''))
    return {'success': True, 'item': item}


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


@app.get('/api/statements/latest/{statement_type}/download')
def download_latest_statement(statement_type: str):
    if statement_type not in STATEMENT_TYPES:
        raise HTTPException(status_code=404, detail='Loại sao kê không hợp lệ')
    init_db()
    cleanup_expired()
    with get_conn() as conn:
        row = conn.execute('SELECT original_name, path FROM statement_uploads WHERE statement_type = ? ORDER BY uploaded_at DESC LIMIT 1', (statement_type,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Chưa có file sao kê')
    path = Path(row['path'])
    if not path.exists():
        raise HTTPException(status_code=404, detail='File CSV không còn tồn tại trên máy chủ')
    return FileResponse(path, filename=row['original_name'], media_type='application/octet-stream')


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
