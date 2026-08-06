# AGENTS.md — File Attachments

## Project identity
- Tên: File Attachments
- Slug: file-attachments-webapp
- Đường dẫn: /home/locdodoan/.openclaw/workspace/file-attachments-webapp
- Port: 8890
- Service: file-attachments-webapp
- Trạng thái: active

## Purpose
File upload/download, Windows/Samba helper commands should be copyable in UI.

## User and UX rules
- Người dùng chính: cán bộ nội bộ, ưu tiên thao tác nhanh, ít lỗi.
- UI hiện đại, sạch, card-based.
- Gradient ưu tiên: cyan → indigo.
- Font ưu tiên: Plus Jakarta Sans.
- Mobile tuyệt đối không tràn ngang, không gây side zoom.
- Form và upload file phải có tap target lớn, dễ bấm.
- Với app liên quan Windows/Samba, hiển thị sẵn lệnh Run/PowerShell copy được trong UI.

## Hermes working rules
1. Trước khi sửa code: chạy `git status`.
2. Trước thay đổi lớn: commit backup hoặc tạo checkpoint.
3. Không xoá dữ liệu thật nếu chưa backup.
4. Nếu đổi database schema: backup DB trước, ghi rõ migration/rollback.
5. Sau khi sửa: test chức năng chính, kiểm tra log/service nếu có.
6. Cập nhật `docs/CHANGELOG.md` cho thay đổi quan trọng.
7. Commit với message rõ ràng: `feat:`, `fix:`, `docs:`, `chore:`, `backup:`.

## Common commands
```bash
git status
python3 server.py
systemctl --user status file-attachments-webapp --no-pager
systemctl --user restart file-attachments-webapp
journalctl --user -u file-attachments-webapp -n 100 --no-pager
```

## Rollback
```bash
git log --oneline
git reset --hard <commit_id>
systemctl --user restart file-attachments-webapp
```

Xem thêm: `docs/CONTEXT.md`, `docs/RUNBOOK.md`, `docs/ROLLBACK.md`.
