# Rollback — File Attachments

## Rollback code bằng Git
```bash
cd /home/locdodoan/.openclaw/workspace/file-attachments-webapp
git log --oneline -20
git reset --hard <commit_id>
systemctl --user restart file-attachments-webapp
```

## Rollback an toàn bằng revert
```bash
cd /home/locdodoan/.openclaw/workspace/file-attachments-webapp
git revert <commit_id>
systemctl --user restart file-attachments-webapp
```

## Rollback dữ liệu
1. Dừng service nếu có.
2. Copy DB từ `/home/locdodoan/backups/webapps/...` hoặc `backups/` của app.
3. Start lại service.
4. Kiểm tra log.
