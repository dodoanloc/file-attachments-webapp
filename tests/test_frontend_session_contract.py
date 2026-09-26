from pathlib import Path


APP_JS = Path(__file__).parents[1] / "public" / "app.js"


def test_apply_session_uses_memory_only_credentials():
    source = APP_JS.read_text()
    start = source.index("function applySession()")
    end = source.index("\nasync function login()", start)
    block = source[start:end]

    assert "localStorage.getItem(STORAGE_KEY)" not in block
    assert "!!session?.username && !!session?.password" in block


def test_password_is_not_serialized_to_browser_storage():
    source = APP_JS.read_text()
    assert "localStorage.setItem(STORAGE_KEY, JSON.stringify(session))" not in source
    assert "JSON.stringify({ username, role: session.role })" in source


def test_archive_password_remember_uses_session_storage_only():
    source = APP_JS.read_text()
    assert "file-attachments-mis-archive-password" in source
    assert "sessionStorage.setItem('file-attachments-mis-archive-password'" in source
    assert "localStorage.setItem('file-attachments-mis-archive-password'" not in source
    assert "els.misRememberArchivePassword" in source
