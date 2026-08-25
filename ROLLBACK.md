# Rollback

Production deploy is approval-gated. Keep previous release SHA before restart.

```bash
# inspect current service and release
systemctl --user status <service>.service
git rev-parse HEAD

# restore code to a known-good release in a disposable worktree
git fetch origin
git worktree add /tmp/rollback-<app> <KNOWN_GOOD_SHA>

# backup DB before any data operation
cp data/*.db backups/  # adapt to app; never overwrite backup

# restart exact service only after review
systemctl --user restart <service>.service
curl -f http://127.0.0.1:<port>/
```

Do not restore DB automatically when schema migrations changed. Preserve logs and commit SHA.
