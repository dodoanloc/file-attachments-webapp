# Runbook — File Attachments

## Đường dẫn
```bash
cd /home/locdodoan/.openclaw/workspace/file-attachments-webapp
```

## Kiểm tra nhanh
```bash
git status
python3 --version
```

## Chạy thủ công
```bash
python3 server.py
```

## Service
```bash
systemctl --user status file-attachments-webapp --no-pager
systemctl --user restart file-attachments-webapp
journalctl --user -u file-attachments-webapp -n 100 --no-pager
```

## Health check
```bash
curl -I http://127.0.0.1:8890 || true
```

## Backup nhanh
```bash
/home/locdodoan/webapps/scripts/backup-webapps.sh file-attachments-webapp
```
