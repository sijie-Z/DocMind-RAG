#!/usr/bin/env bash
# Demo 上线自检（在宿主机执行；外部 URL / Cloudflare Access 检查见 README）。
# 用法：DEMO_PASSWORD='...' ./deploy/demo/verify_demo.sh [http://127.0.0.1:8080]
set -uo pipefail

BASE="${1:-http://127.0.0.1:8080}"
BACKEND="${BACKEND_URL:-http://127.0.0.1:8000}"
EXPECT_INDEX="${DEMO_EXPECT_INDEX:-docmind_demo_knowledge}"
PASS=0
FAIL=0

step() {
  if [ "$1" -eq 0 ]; then
    echo "[PASS] $2"
    PASS=$((PASS + 1))
  else
    echo "[FAIL] $2"
    FAIL=$((FAIL + 1))
  fi
}

# 1) 前端存活
curl -fsS --max-time 10 "$BASE/healthz" | grep -q healthy
step $? "frontend /healthz"

# 2) SPA 可加载
curl -fsS --max-time 10 "$BASE/" | grep -qi 'id="app"'
step $? "frontend SPA 首页"

# 3) 后端健康 + 派生索引身份
HEALTH_JSON="$(curl -fsS --max-time 20 "$BACKEND/health" 2>/dev/null || true)"
JSON="$HEALTH_JSON" python3 - "$EXPECT_INDEX" <<'PY'
import json, os, sys
try:
    d = json.loads(os.environ.get("JSON") or "{}")
    kb = d.get("knowledge_base") or {}
    assert d.get("status") == "healthy", d.get("status")
    assert kb.get("index_name") == sys.argv[1], kb.get("index_name")
except Exception as exc:  # noqa: BLE001 - 自检脚本要把原因打出来
    print(f"health check failed: {exc}")
    raise SystemExit(1)
PY
step $? "backend /health healthy 且 index_name=$EXPECT_INDEX"

# 4) 演示账号可登录（未提供 DEMO_PASSWORD 时仅提示）
if [ -n "${DEMO_PASSWORD:-}" ]; then
  python3 - "$BASE" <<'PY'
import json, os, sys, urllib.error, urllib.request

base = sys.argv[1]
req = urllib.request.Request(
    f"{base}/api/v1/auth/login/json",
    data=json.dumps(
        {
            "username": os.environ.get("DEMO_USERNAME", "docmind_demo"),
            "password": os.environ["DEMO_PASSWORD"],
        }
    ).encode(),
    headers={"Content-Type": "application/json", "User-Agent": "docmind-verify"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as r:
    data = json.load(r)
assert (data.get("data") or {}).get("access_token")
PY
  step $? "演示账号登录"
else
  echo "[SKIP] 演示账号登录（未设置 DEMO_PASSWORD）"
fi

# 5) 上传体积限制：11MB 应被拒绝（后端 MAX_FILE_SIZE=10MB）
if [ -n "${DEMO_PASSWORD:-}" ]; then
  python3 - "$BASE" <<'PY'
import json, os, sys, urllib.error, urllib.request

base = sys.argv[1]
req = urllib.request.Request(
    f"{base}/api/v1/auth/login/json",
    data=json.dumps(
        {
            "username": os.environ.get("DEMO_USERNAME", "docmind_demo"),
            "password": os.environ["DEMO_PASSWORD"],
        }
    ).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=20) as r:
    token = json.load(r)["data"]["access_token"]

boundary = "----docmindverify"
big = b"x" * (11 * 1024 * 1024)
body = (
    f"--{boundary}\r\n"
    'Content-Disposition: form-data; name="file"; filename="too-big.txt"\r\n'
    "Content-Type: text/plain\r\n\r\n"
).encode() + big + f"\r\n--{boundary}--\r\n".encode()
req = urllib.request.Request(
    f"{base}/api/v1/documents/upload",
    data=body,
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        raise SystemExit(f"未拒绝超大上传: HTTP {r.status}")
except urllib.error.HTTPError as exc:
    assert exc.code in (400, 413), exc.code
PY
  step $? "11MB 上传被拒绝"
else
  echo "[SKIP] 上传体积限制（未设置 DEMO_PASSWORD）"
fi

# 6) 外部 URL（可选）：Access 保护下通常返回 302/401/403
if [ -n "${EXTERNAL_URL:-}" ]; then
  CODE="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$EXTERNAL_URL" || echo 000)"
  case "$CODE" in
    200 | 302 | 401 | 403) step 0 "外部 URL $EXTERNAL_URL -> HTTP $CODE" ;;
    *) step 1 "外部 URL $EXTERNAL_URL -> HTTP $CODE" ;;
  esac
fi

echo
echo "RESULT: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
