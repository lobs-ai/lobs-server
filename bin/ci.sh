#!/usr/bin/env bash
# ci.sh — Universal build & test script for all projects
# Called by workflow nodes after agent work completes.
# Auto-detects project type and runs appropriate build + test.
#
# Usage: ci.sh [project_dir]
# Exit codes: 0 = pass, 1 = fail, 2 = no buildable project detected
#
# Projects can override with their own bin/ci.sh or scripts/ci.sh

set -euo pipefail

PROJECT_DIR="${1:-.}"
cd "$PROJECT_DIR"

echo "=== CI: $(basename "$PWD") ==="
echo "=== CI: directory: $PWD ==="
echo "=== CI: started at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# ── Project-level override ──────────────────────────────────────────
SELF="$(realpath "$0" 2>/dev/null || echo "$0")"
if [[ -x "bin/ci.sh" ]] && [[ "$(realpath bin/ci.sh 2>/dev/null)" != "$SELF" ]]; then
    echo "=== CI: using project-local bin/ci.sh ==="
    exec bin/ci.sh
fi
if [[ -x "scripts/ci.sh" ]]; then
    echo "=== CI: using project-local scripts/ci.sh ==="
    exec scripts/ci.sh
fi

ERRORS=0
fail() { echo "FAIL: $1"; ERRORS=$((ERRORS + 1)); }
pass() { echo "PASS: $1"; }

DETECTED=""

# ── Swift SPM ────────────────────────────────────────────────────────
if [[ -f "Package.swift" ]]; then
    DETECTED="swift-spm"
    echo "=== CI: detected Swift SPM project ==="
    echo "--- BUILD ---"
    if swift build 2>&1; then pass "swift build"; else fail "swift build"; fi
    echo "--- TEST ---"
    if swift test 2>&1; then pass "swift test"; else fail "swift test"; fi

# ── Xcode project (iOS/macOS) ────────────────────────────────────────
elif ls *.xcodeproj &>/dev/null || ls *.xcworkspace &>/dev/null; then
    DETECTED="xcode"
    echo "=== CI: detected Xcode project ==="

    PROJECT=""
    if ls *.xcodeproj &>/dev/null; then PROJECT="$(ls -d *.xcodeproj | head -1)"; fi

    SCHEME=""
    if [[ -n "$PROJECT" ]]; then
        SCHEME=$(xcodebuild -project "$PROJECT" -list 2>/dev/null \
            | awk '/Schemes:/{f=1;next} f && /^[[:space:]]/{print;next} f{exit}' \
            | head -1 | xargs)
    fi
    [[ -z "$SCHEME" ]] && SCHEME="$(basename "$PWD")"

    BUILD_ARGS=()
    [[ -n "$PROJECT" ]] && BUILD_ARGS+=(-project "$PROJECT")
    BUILD_ARGS+=(-scheme "$SCHEME")

    # Detect iOS vs macOS
    if [[ -n "$PROJECT" ]] && grep -q "SDKROOT.*iphoneos\|TARGETED_DEVICE_FAMILY" "$PROJECT/project.pbxproj" 2>/dev/null; then
        BUILD_ARGS+=(-sdk iphonesimulator -destination "platform=iOS Simulator,name=iPhone 16,OS=latest")
        echo "=== CI: targeting iOS Simulator ==="
    else
        BUILD_ARGS+=(-destination "platform=macOS")
        echo "=== CI: targeting macOS ==="
    fi

    echo "--- BUILD ---"
    BUILD_OUTPUT=$(xcodebuild "${BUILD_ARGS[@]}" build CODE_SIGNING_ALLOWED=NO 2>&1) || true
    echo "$BUILD_OUTPUT" | tail -50
    if echo "$BUILD_OUTPUT" | grep -q "BUILD SUCCEEDED"; then
        pass "xcodebuild build"
    else
        fail "xcodebuild build"
    fi

# ── Python ────────────────────────────────────────────────────────────
elif [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]] || [[ -f "requirements.txt" ]]; then
    DETECTED="python"
    export ORCHESTRATOR_ENABLED=false  # prevent test DB contention with live server
    echo "=== CI: detected Python project ==="
    [[ -d ".venv/bin" ]] && source .venv/bin/activate
    echo "--- TEST ---"
    if [[ -d "tests" ]] || compgen -G "test_*.py" >/dev/null 2>&1; then
        if python3 -m pytest --tb=short -q 2>&1; then pass "pytest"; else fail "pytest"; fi
    else
        echo "SKIP: no tests directory"
    fi

# ── Node.js ───────────────────────────────────────────────────────────
elif [[ -f "package.json" ]]; then
    DETECTED="node"
    echo "=== CI: detected Node.js project ==="
    if [[ ! -d "node_modules" ]]; then
        echo "--- INSTALL ---"
        npm ci 2>&1 || npm install 2>&1
    fi
    echo "--- BUILD ---"
    if grep -q '"build"' package.json; then
        if npm run build 2>&1; then pass "npm build"; else fail "npm build"; fi
    else echo "SKIP: no build script"; fi
    echo "--- TEST ---"
    if grep -q '"test"' package.json; then
        if npm test 2>&1; then pass "npm test"; else fail "npm test"; fi
    else echo "SKIP: no test script"; fi

# ── Rust ──────────────────────────────────────────────────────────────
elif [[ -f "Cargo.toml" ]]; then
    DETECTED="rust"
    echo "=== CI: detected Rust project ==="
    echo "--- BUILD ---"
    if cargo build 2>&1; then pass "cargo build"; else fail "cargo build"; fi
    echo "--- TEST ---"
    if cargo test 2>&1; then pass "cargo test"; else fail "cargo test"; fi

# ── Go ────────────────────────────────────────────────────────────────
elif [[ -f "go.mod" ]]; then
    DETECTED="go"
    echo "=== CI: detected Go project ==="
    echo "--- BUILD ---"
    if go build ./... 2>&1; then pass "go build"; else fail "go build"; fi
    echo "--- TEST ---"
    if go test ./... 2>&1; then pass "go test"; else fail "go test"; fi
fi

# ── Results ──────────────────────────────────────────────────────────
echo ""
echo "=== CI: finished at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [[ -z "$DETECTED" ]]; then
    echo "=== CI: no buildable project detected ==="
    exit 2
fi

if [[ $ERRORS -gt 0 ]]; then
    echo "=== CI: FAILED ($ERRORS error(s)) ==="
    exit 1
else
    echo "=== CI: PASSED ==="
    exit 0
fi
