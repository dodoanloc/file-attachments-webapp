# Quản lý tệp đính kèm

**Repo:** `file-attachments-webapp`  
**Mục đích:** Tải lên, quản lý, tìm kiếm và tải xuống tệp đính kèm; hỗ trợ theo dõi hạn xử lý statement.

## Người dùng nên biết

- Đây là mã nguồn ứng dụng nội bộ; dữ liệu runtime, DB, upload và secret không thuộc source code cần commit.
- Thay đổi chức năng phải đi qua branch → Pull Request → CI → reviewer `ktnqagribanktx-lang` → merge.
- Production không deploy trực tiếp từ máy local. Xem [`ROLLBACK.md`](ROLLBACK.md).

## Công nghệ và luồng chính

Python server + static HTML/CSS/JS.

```text
Người dùng → giao diện frontend → backend/API hoặc server → DB/tệp cấu hình
```

## Cấu trúc repo

```text
├── server.py
├── statement_due.py
├── public/
├── docs/
├── docs/
├── .github/
└── ROLLBACK.md
```

### Thành phần chính

- `server.py`: thành phần cần biết khi sửa app.
- `statement_due.py`: thành phần cần biết khi sửa app.
- `public/`: thành phần cần biết khi sửa app.
- `docs/`: thành phần cần biết khi sửa app.

## Chạy và triển khai

- **Service:** `file-attachments-8894.service`
- **Port ghi nhận:** `8894`
- Kiểm tra service: `systemctl --user status file-attachments-8894.service`
- Không sửa trực tiếp DB production khi chưa backup.

## Kiểm tra trước Pull Request

```bash
# Python nếu repo có file .py
python -m py_compile <changed-python-files>

# Node nếu repo có package.json/server.js
node --check <changed-js-file>

# Test riêng của repo nếu có
pytest -q   # hoặc npm test
```

## Quyền và dữ liệu

- Không commit token, password, API key, session, DB production, upload hoặc PII.
- Giữ nguyên phân quyền hiện hữu khi sửa API/UI.
- Với thay đổi schema hoặc file lưu trữ: backup trước, cập nhật runbook, test rollback.
