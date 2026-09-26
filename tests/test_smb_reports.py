from __future__ import annotations

from pathlib import Path

import pytest

import server


def test_parse_mis_listing_keeps_only_zip_files_and_sorts_newest_first():
    output = """
  BAO_CAO_CU.txt                         A      123  Mon Sep 01 08:10:00 2026
  BAO_CAO_20260902.zip                   A     4096  Tue Sep 02 09:30:00 2026
  BAO_CAO_20260901.ZIP                   A     2048  Mon Sep 01 10:00:00 2026
"""
    reports = server.parse_mis_listing(output, folder="20260902")
    assert [item["name"] for item in reports] == [
        "20260902/BAO_CAO_20260902.zip",
        "20260902/BAO_CAO_20260901.ZIP",
    ]
    assert reports[0]["size"] == 4096


def test_copy_mis_report_creates_normal_attachment_without_keeping_authfile(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(server, "DB_PATH", tmp_path / "attachments.db")
    server.init_db()

    def fake_run_smb(*, username, password, command):
        assert username == "corp.agribank.com.vn\\locdodoan"
        assert password == "secret"
        assert 'get "20260902/BAO_CAO.zip"' in command
        destination = Path(command.rsplit('"', 2)[1])
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"report")
        return "getting file"

    monkeypatch.setattr(server, "run_mis_smb", fake_run_smb)
    item = server.copy_mis_report(
        username="corp.agribank.com.vn\\locdodoan",
        password="secret",
        report_name="20260902/BAO_CAO.zip",
        uploaded_by="locdodoan",
    )

    assert item["original_name"] == "BAO_CAO.zip"
    assert item["size"] == 6
    with server.get_conn() as conn:
        stored_name = conn.execute("SELECT stored_name FROM attachments WHERE id = ?", (item["id"],)).fetchone()[0]
    assert (server.UPLOAD_DIR / stored_name).read_bytes() == b"report"
    assert not list(tmp_path.rglob("*.smb-auth"))


def test_copy_mis_report_rejects_traversal_name():
    with pytest.raises(server.HTTPException) as exc:
        server.validate_mis_report_name("../secret.xlsx")
    assert exc.value.status_code == 400


def test_normalize_ad_credentials_defaults_to_agribank_domain():
    assert server.normalize_ad_credentials("locdodoan") == ("locdodoan", "corp.agribank.com.vn")
    assert server.normalize_ad_credentials("CORP.AGRIBANK.COM.VN\\locdodoan") == ("locdodoan", "CORP.AGRIBANK.COM.VN")
    assert server.normalize_ad_credentials("locdodoan@corp.agribank.com.vn") == ("locdodoan", "corp.agribank.com.vn")


def test_smb_auth_file_uses_split_username_and_domain(tmp_path, monkeypatch):
    seen = {}
    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""
    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        auth = Path(argv[argv.index('-A') + 1]).read_text()
        seen["auth"] = auth
        return Result()
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    monkeypatch.setattr(server.tempfile, "mkstemp", lambda **_: (
        __import__("os").open(tmp_path / "auth", __import__("os").O_CREAT | __import__("os").O_RDWR), str(tmp_path / "auth")))
    assert server.run_mis_smb(username="corp.agribank.com.vn\\locdodoan", password="secret", command="ls") == "ok"
    assert seen["argv"][1] == "//10.0.43.21/mis"
    assert "-g" in seen["argv"]
    assert seen["argv"][-1] == 'cd "ChiNhanh/cn3511/MSSR08/3511"; ls'
    assert "username=locdodoan\n" in seen["auth"]
    assert "domain=corp.agribank.com.vn\n" in seen["auth"]
    assert not (tmp_path / "auth").exists()


def test_latest_mis_date_folder_uses_newest_smb_modified_time_not_largest_name():
    listing = """
  20260902                            D        0  Tue Sep 02 09:00:00 2026
  temp                                D        0  Tue Sep 02 12:00:00 2026
  20260901                            D        0  Wed Sep 03 10:30:00 2026
  20250831                            D        0  Tue Sep 02 09:00:00 2026
"""
    assert server.latest_mis_date_folder(listing) == "20260901"


def test_latest_mis_date_folder_breaks_equal_modified_time_by_folder_name():
    listing = """
  20260901                            D        0  Tue Sep 02 09:00:00 2026
  20260902                            D        0  Tue Sep 02 09:00:00 2026
"""
    assert server.latest_mis_date_folder(listing) == "20260902"


def test_list_mis_reports_skips_newest_empty_date_folder(monkeypatch):
    root_listing = """
  20260903                            D        0  Wed Sep 03 10:00:00 2026
  20260902                            D        0  Tue Sep 02 10:00:00 2026
"""
    listings = {
        'ls': root_listing,
        'cd "20260903"; ls': '  README.txt                            A      12  Wed Sep 03 10:00:00 2026\n',
        'cd "20260902"; ls': '  BAO_CAO_20260902.zip                 A    4096  Tue Sep 02 10:00:00 2026\n',
    }
    commands = []

    def fake_run_smb(*, username, password, command):
        commands.append(command)
        return listings[command]

    monkeypatch.setattr(server, 'run_mis_smb', fake_run_smb)
    reports = server.list_mis_reports(username='user', password='secret')
    assert [item['name'] for item in reports] == ['20260902/BAO_CAO_20260902.zip']
    assert commands == ['ls', 'cd "20260903"; ls', 'cd "20260902"; ls']


def test_parse_mis_zip_listing_returns_only_zip_files():
    listing = """
  BAO_CAO_A.zip                       A     4096  Tue Sep 02 09:30:00 2026
  BAO_CAO_B.ZIP                       A     2048  Tue Sep 02 09:31:00 2026
  README.txt                          A      123  Tue Sep 02 09:32:00 2026
"""
    assert [x["name"] for x in server.parse_mis_listing(listing)] == ["BAO_CAO_B.ZIP", "BAO_CAO_A.zip"]


def test_parse_mis_zip_listing_accepts_smbclient_grepable_format():
    listing = """
BAO_CAO_A.zip|A|4096|Tue Sep 02 09:30:00 2026
BAO_CAO_B.ZIP|A|2048|Tue Sep 02 09:31:00 2026
README.txt|A|123|Tue Sep 02 09:32:00 2026
"""
    assert [x["name"] for x in server.parse_mis_listing(listing)] == ["BAO_CAO_B.ZIP", "BAO_CAO_A.zip"]


def test_mis_date_folders_accept_smbclient_grepable_format():
    listing = """
20260903|D|0|Wed Sep 03 10:00:00 2026
20260902|D|0|Tue Sep 02 10:00:00 2026
"""
    assert server.mis_date_folders_newest_first(listing) == ["20260903", "20260902"]


def test_mis_parsers_tolerate_unparseable_smb_dates():
    folders = """
  20260903                            D        0  03-09-2026 10:00
  20260902                            D        0  02-09-2026 10:00
"""
    files = """
  Bao cao ngay 03-09.zip               A     4096  03-09-2026 10:00
"""
    assert server.mis_date_folders_newest_first(folders) == ["20260903", "20260902"]
    assert [x['name'] for x in server.parse_mis_listing(files, folder='20260903')] == [
        '20260903/Bao cao ngay 03-09.zip'
    ]


def test_parse_mis_listing_tolerates_compact_pipe_format():
    listing = 'Bao cao.zip|4096|03-09-2026 10:00|A\n'
    assert [x['name'] for x in server.parse_mis_listing(listing)] == ['Bao cao.zip']


def test_validate_mis_report_name_accepts_only_date_folder_zip():
    assert server.validate_mis_report_name("20260902/BAO_CAO.zip") == "20260902/BAO_CAO.zip"
    for value in ["BAO_CAO.zip", "20260902/../secret.zip", "20260902/README.txt", "notdate/BAO_CAO.zip"]:
        with pytest.raises(server.HTTPException):
            server.validate_mis_report_name(value)


def test_extract_csv_attachment_returns_zip_when_password_empty(tmp_path):
    import zipfile
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("report.csv", "a,b\n1,2\n")
    result = server.extract_csv_attachment(archive, "", tmp_path / "uploads")
    assert result == archive


def test_extract_csv_attachment_extracts_csv_with_password(tmp_path):
    import zipfile
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/report.csv", "a,b\n1,2\n")
    result = server.extract_csv_attachment(archive, "secret", tmp_path / "uploads")
    assert result.name == "report.csv"
    assert result.read_text() == "a,b\n1,2\n"
    assert not archive.exists()


def test_extract_csv_attachment_rejects_archive_without_csv(tmp_path):
    import zipfile
    archive = tmp_path / "report.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("report.xlsx", "x")
    with pytest.raises(server.HTTPException, match="CSV"):
        server.extract_csv_attachment(archive, "secret", tmp_path / "uploads")
