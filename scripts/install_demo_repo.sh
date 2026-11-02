#!/usr/bin/env bash
# Create a small local Git fixture repository for the Milestone 1 demo.
#
# The repository contains a real failing test: app/calculator.py implements
# add() as subtraction. The agent clones it, edits the implementation, runs
# pytest in the sandbox, and reports the verified diff.
#
# The repository is exposed to the agent as: fixture://demo-bugfix
set -euo pipefail

HOST_ROOT="${IO_AGENT_HOST_ROOT:-/srv/io-agent}"
REPOS_DIR="$HOST_ROOT/repos"
BARE_REPO="$REPOS_DIR/demo-bugfix.git"
FORCE="${FORCE:-0}"

sudo -n mkdir -p "$REPOS_DIR"
sudo -n chmod 755 "$REPOS_DIR"

if [ -d "$BARE_REPO" ] && [ "$FORCE" != "1" ]; then
  echo "Fixture repository already exists: $BARE_REPO (use FORCE=1 to recreate)"
  exit 0
fi

if [ -d "$BARE_REPO" ] && [ "$FORCE" = "1" ]; then
  echo "Removing existing fixture repository because FORCE=1"
  sudo -n rm -rf "$BARE_REPO"
fi

workdir="$(mktemp -d)"
stage="$(mktemp -d)"
trap 'rm -rf "$workdir" "$stage"' EXIT

mkdir -p "$workdir/app" "$workdir/tests"

cat > "$workdir/app/__init__.py" <<'EOF'
EOF

cat > "$workdir/app/calculator.py" <<'EOF'
def add(a, b):
    return a - b
EOF

cat > "$workdir/tests/__init__.py" <<'EOF'
EOF

cat > "$workdir/tests/test_calculator.py" <<'EOF'
from app.calculator import add


def test_add():
    assert add(2, 3) == 5
EOF

cat > "$workdir/requirements.txt" <<'EOF'
pytest==8.3.4
EOF

cat > "$workdir/README.md" <<'EOF'
# Demo bugfix fixture

`app/calculator.py` implements `add()` incorrectly as subtraction.
`tests/test_calculator.py` describes the expected behaviour.

Run the suite with:

```bash
python -m pytest -q
```
EOF

git -C "$workdir" init -q -b main
git -C "$workdir" add .
git -C "$workdir" -c user.name="IO Agent Demo" -c user.email="demo@infoorigin.local" commit -q -m "Add calculator with a failing add test"
git clone -q --bare "$workdir" "$stage/demo-bugfix.git"
# $REPOS_DIR is root-owned and read-only for the agent by design, so stage the
# bare repository as the current user and install it with sudo.
sudo -n rm -rf "$BARE_REPO"
sudo -n cp -a "$stage/demo-bugfix.git" "$BARE_REPO"
sudo -n chmod -R a+rX "$BARE_REPO"

commit="$(git -C "$BARE_REPO" rev-parse --short HEAD)"
echo "Created fixture repository: $BARE_REPO (commit $commit)"
echo "Agent reference: fixture://demo-bugfix"
