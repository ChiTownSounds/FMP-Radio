#!/bin/bash
# deploy_vm.sh - Playout Server Auto-Deployment script
set -e

REPO_DIR="/home/ubuntu/FMP-Radio"
echo "[*] Deploying FMP-Radio VM updates..."

# 1. Navigate to repo
cd "$REPO_DIR"

# 2. Fetch origin changes
echo "[*] Fetching changes from GitHub..."
git fetch origin

# 3. Stash local state (to keep untracked logs/backups safe)
echo "[*] Stashing local changes..."
git stash --include-untracked || true

# 4. Force reset to origin/main (to align with GitHub main)
echo "[*] Resetting working tree to origin/main..."
git reset --hard origin/main

# 5. Restore local state
echo "[*] Restoring stashed local state..."
git stash pop || true

# 6. Install pre-commit hook on VM as well (to prevent path pollution)
echo "[*] Installing pre-commit path guard on VM..."
mkdir -p .git/hooks
cat << 'EOF' > .git/hooks/pre-commit
#!/bin/sh
if command -v python3 >/dev/null 2>&1; then
    python3 tools/git_pre_commit_guard.py
else
    echo "[PRE-COMMIT GUARD] Python 3 not found. Commit aborted."
    exit 1
fi
EOF
chmod +x .git/hooks/pre-commit

# 7. Restart service
echo "[*] Restarting fmp-radio service..."
sudo systemctl restart fmp-radio

echo "[✓] Deployment Completed successfully!"
