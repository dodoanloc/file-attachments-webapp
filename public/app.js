const MAX_FILE_SIZE = 500 * 1024 * 1024;
const STORAGE_KEY = 'file_attachments_user';
const WINDOWS_EXPLORER_UNC = '\\\\10.28.10.161\\file-upload';
const WINDOWS_EXPLORER_FILE_URL = 'file://///10.28.10.161/file-upload';
const WINDOWS_EXPLORER_SHORTCUT = '/Mo-thu-muc-file-upload.cmd';

const els = {
  loginCard: document.getElementById('loginCard'),
  appCard: document.getElementById('appCard'),
  listCard: document.getElementById('listCard'),
  usernameInput: document.getElementById('usernameInput'),
  passwordInput: document.getElementById('passwordInput'),
  loginBtn: document.getElementById('loginBtn'),
  logoutBtn: document.getElementById('logoutBtn'),
  loginStatus: document.getElementById('loginStatus'),
  uploadStatus: document.getElementById('uploadStatus'),
  currentUser: document.getElementById('currentUser'),
  fileInput: document.getElementById('fileInput'),
  uploadBtn: document.getElementById('uploadBtn'),
  refreshBtn: document.getElementById('refreshBtn'),
  openExplorerBtn: document.getElementById('openExplorerBtn'),
  explorerStatus: document.getElementById('explorerStatus'),
  copyRunCommandBtn: document.getElementById('copyRunCommandBtn'),
  runCommandText: document.getElementById('runCommandText'),
  filesBody: document.getElementById('filesBody'),
};

let session = null;

function setStatus(el, text, type = 'info') {
  el.className = `status ${type}`;
  el.textContent = text;
}

function fmtBytes(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GB`;
  if (n >= 1024 ** 2) return `${(n / 1024 ** 2).toFixed(2)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

function fmtDate(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat('vi-VN', {
    timeZone: 'Asia/Ho_Chi_Minh', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'
  }).format(d).replace(',', '');
}

function esc(value) {
  return String(value ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function authQuery() {
  return `username=${encodeURIComponent(session.username)}&password=${encodeURIComponent(session.password)}`;
}

function applySession() {
  session = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
  const loggedIn = !!session?.username && !!session?.password;
  els.loginCard.classList.toggle('hidden', loggedIn);
  els.appCard.classList.toggle('hidden', !loggedIn);
  els.listCard.classList.toggle('hidden', !loggedIn);
  els.logoutBtn.classList.toggle('hidden', !loggedIn);
  if (loggedIn) {
    els.currentUser.textContent = `Đang đăng nhập: ${session.username}${session.role ? ` (${session.role})` : ''}`;
    loadFiles();
  }
}

async function login() {
  const username = els.usernameInput.value.trim();
  const password = els.passwordInput.value;
  if (!username || !password) return setStatus(els.loginStatus, 'Nhập tài khoản và mật khẩu.', 'error');
  els.loginBtn.disabled = true;
  setStatus(els.loginStatus, 'Đang đăng nhập...', 'info');
  try {
    const res = await fetch('/api/login', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.detail || 'Đăng nhập thất bại');
    session = { username, password, role: json.user?.role || '' };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
    setStatus(els.loginStatus, 'Đăng nhập thành công.', 'success');
    applySession();
  } catch (err) {
    setStatus(els.loginStatus, err.message || 'Đăng nhập thất bại.', 'error');
  } finally {
    els.loginBtn.disabled = false;
  }
}

function logout() {
  localStorage.removeItem(STORAGE_KEY);
  session = null;
  els.filesBody.innerHTML = '<tr><td colspan="6" class="empty">Chưa đăng nhập.</td></tr>';
  applySession();
}

async function loadFiles() {
  if (!session) return;
  els.filesBody.innerHTML = '<tr><td colspan="6" class="empty">Đang tải danh sách...</td></tr>';
  try {
    const res = await fetch(`/api/files?${authQuery()}`);
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.detail || 'Không tải được danh sách file');
    renderFiles(json.items || []);
  } catch (err) {
    els.filesBody.innerHTML = `<tr><td colspan="6" class="empty">${esc(err.message || 'Lỗi tải danh sách.')}</td></tr>`;
  }
}

function renderFiles(items) {
  if (!items.length) {
    els.filesBody.innerHTML = '<tr><td colspan="6" class="empty">Chưa có file đính kèm nào.</td></tr>';
    return;
  }
  els.filesBody.innerHTML = items.map(item => `
    <tr>
      <td><div class="file-name" title="${esc(item.original_name)}">${esc(item.original_name)}</div></td>
      <td>${esc(fmtBytes(item.size))}</td>
      <td>${esc(fmtDate(item.uploaded_at))}</td>
      <td>${esc(item.uploaded_by)}</td>
      <td>${esc(fmtDate(item.expires_at))}</td>
      <td class="actions">
        <button class="ghost" type="button" data-file-id="${esc(item.id)}" data-file-name="${esc(item.original_name)}" onclick="downloadFile(this.dataset.fileId, this.dataset.fileName)">Tải xuống</button>
        <button class="danger" type="button" onclick="deleteFile('${esc(item.id)}')">Xoá</button>
      </td>
    </tr>
  `).join('');
}

async function uploadFile() {
  const files = Array.from(els.fileInput.files || []);
  if (!files.length) return setStatus(els.uploadStatus, 'Chọn một hoặc nhiều file/ảnh trước khi tải lên.', 'error');

  const oversized = files.filter(file => file.size > MAX_FILE_SIZE);
  if (oversized.length) {
    return setStatus(
      els.uploadStatus,
      `Có ${oversized.length} file vượt quá giới hạn 500 MB: ${oversized.map(file => file.name).join(', ')}`,
      'error'
    );
  }

  els.uploadBtn.disabled = true;
  const uploaded = [];
  const failed = [];
  try {
    for (let i = 0; i < files.length; i += 1) {
      const file = files[i];
      const fd = new FormData();
      fd.append('username', session.username);
      fd.append('password', session.password);
      fd.append('file', file);
      setStatus(
        els.uploadStatus,
        `Đang tải lên ${i + 1}/${files.length}: ${file.name} (${fmtBytes(file.size)})...`,
        'info'
      );
      try {
        const res = await fetch('/api/files', { method: 'POST', body: fd });
        const json = await res.json();
        if (!res.ok || !json.success) throw new Error(json.detail || 'Tải lên thất bại');
        uploaded.push(file.name);
      } catch (err) {
        failed.push(`${file.name}: ${err.message || 'Tải lên thất bại'}`);
      }
    }

    els.fileInput.value = '';
    if (failed.length) {
      setStatus(
        els.uploadStatus,
        `Đã tải lên ${uploaded.length}/${files.length} file. Lỗi: ${failed.join('; ')}`,
        uploaded.length ? 'info' : 'error'
      );
    } else {
      setStatus(els.uploadStatus, `Tải lên thành công ${uploaded.length} file. File sẽ tự xoá sau 7 ngày.`, 'success');
    }
    await loadFiles();
  } finally {
    els.uploadBtn.disabled = false;
  }
}

async function openWindowsExplorer() {
  const msg = `Đường dẫn Windows Explorer: ${WINDOWS_EXPLORER_UNC}`;
  try {
    await navigator.clipboard?.writeText(WINDOWS_EXPLORER_UNC);
  } catch (_) {
    // Clipboard can be blocked on non-HTTPS; opening through file:// still runs below.
  }

  const a = document.createElement('a');
  a.href = WINDOWS_EXPLORER_FILE_URL;
  a.target = '_blank';
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();

  setStatus(
    els.explorerStatus,
    `${msg}. Đã gửi lệnh mở Windows Explorer. Nếu trình duyệt hỏi quyền mở File Explorer, chọn Mở/Open. Nếu bị chặn, đường dẫn đã được copy để dán vào thanh địa chỉ Explorer.`,
    'success'
  );

  setTimeout(() => {
    const fallback = document.createElement('a');
    fallback.href = WINDOWS_EXPLORER_SHORTCUT;
    fallback.download = 'Mo-thu-muc-file-upload.cmd';
    fallback.textContent = 'Tải file mở nhanh dự phòng';
    fallback.className = 'fallback-link';
    els.explorerStatus.append(' ');
    els.explorerStatus.appendChild(fallback);
  }, 600);
}

function triggerBrowserDownload(url) {
  const a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener';
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function downloadFile(id, originalName = 'file') {
  if (!session?.username || !session?.password) {
    alert('Vui lòng đăng nhập lại trước khi tải file.');
    return;
  }
  const url = new URL(`/api/files/${encodeURIComponent(id)}/download`, window.location.href);
  url.searchParams.set('username', session.username);
  url.searchParams.set('password', session.password);

  const isInternetDomain = window.location.hostname === 'file.agrithoxuan.id.vn';
  const canNativeShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function';

  if (isInternetDomain && canNativeShare) {
    try {
      const res = await fetch(url.toString(), { credentials: 'same-origin' });
      if (!res.ok) throw new Error('Không tải được file để chia sẻ.');
      const blob = await res.blob();
      const file = new File([blob], originalName || 'file', { type: blob.type || 'application/octet-stream' });
      if (typeof navigator.canShare === 'function' && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: originalName || 'File đính kèm' });
        return;
      }
    } catch (err) {
      if (err?.name === 'AbortError') return;
      console.warn('Native share failed, fallback to browser download:', err);
    }
  }

  triggerBrowserDownload(url.toString());
}

async function deleteFile(id) {
  if (!confirm('Xoá file này khỏi máy chủ?')) return;
  try {
    const res = await fetch(`/api/files/${encodeURIComponent(id)}?${authQuery()}`, { method: 'DELETE' });
    const json = await res.json();
    if (!res.ok || !json.success) throw new Error(json.detail || 'Xoá thất bại');
    await loadFiles();
  } catch (err) {
    alert(err.message || 'Xoá thất bại.');
  }
}

async function copyRunCommand() {
  const command = els.runCommandText?.textContent?.trim() || '';
  if (!command) return;
  try {
    await navigator.clipboard.writeText(command);
    setStatus(els.explorerStatus, 'Đã copy lệnh kết nối thư mục file-upload. Mở Run/PowerShell/CMD rồi dán vào để chạy.', 'success');
  } catch (_) {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(els.runCommandText);
    selection.removeAllRanges();
    selection.addRange(range);
    setStatus(els.explorerStatus, 'Không copy tự động được; em đã bôi đen lệnh, bấm Ctrl+C để copy.', 'info');
  }
}

els.loginBtn.addEventListener('click', login);
els.passwordInput.addEventListener('keydown', e => { if (e.key === 'Enter') login(); });
els.logoutBtn.addEventListener('click', logout);
els.uploadBtn.addEventListener('click', uploadFile);
els.refreshBtn.addEventListener('click', loadFiles);
els.openExplorerBtn.addEventListener('click', openWindowsExplorer);
els.copyRunCommandBtn.addEventListener('click', copyRunCommand);
window.downloadFile = downloadFile;
window.deleteFile = deleteFile;
applySession();
