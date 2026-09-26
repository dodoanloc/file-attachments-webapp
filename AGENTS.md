# File Attachments — Agent Context

- Slug: `file-attachments`
- Source: `/home/locdodoan/webapps/projects/file-attachments-webapp`
- Service: `file-attachments-8894.service`
- Port: `8894`
- Runtime: Python FastAPI
- Runtime paths: uploads, backups
- Data class: confidential

## Rules

- Never commit uploaded documents, `.env`, DB dumps, or backups.
- Production checkout is deploy-only; worktrees only for code work.
- Preserve attachment access controls and test with synthetic files only.
- Verify target service plus upload/download behavior after approved deploy.

Registry: `/home/locdodoan/webapps/registry/projects.yaml`
