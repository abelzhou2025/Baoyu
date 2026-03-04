#!/bin/zsh
# meta:source=codex
# meta:created_by=codex-assistant
# meta:owner=abelzhou
# meta:purpose=one-click article visual interpretation pipeline launcher
# meta:verified_at=2026-03-04
set -euo pipefail
export NODE_OPTIONS="--no-deprecation"

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
PIPELINE="$WORKSPACE/scripts/final_md_pipeline.py"

fail_exit() {
  echo "\n失败：$1"
  read -r "?按回车结束..."
  exit 1
}

echo ""
echo "Baoyu 一键图文解读（Gemini Web 生图版）"
echo "------------------------------------------------------------"
echo "- 输入链接后自动识别：博客/长文/视频"
echo "- 生成 1500-2200 字中文深度解读"
echo "- 固定生图：1 张主图(3:1) + 3 张配图(16:9)"
echo "- 输出：1 个最终 Markdown（图片内嵌）"
echo "------------------------------------------------------------"

if ! command -v python3 >/dev/null 2>&1; then
  fail_exit "缺少 python3"
fi

read -r "?请输入链接(URL): " URL
if [[ -z "$URL" || ! "$URL" =~ ^https?:// ]]; then
  fail_exit "URL 不合法"
fi

echo "\n正在处理，请稍候..."
set +e
RESULT="$(python3 "$PIPELINE" \
  --url "$URL" \
  --workspace "$WORKSPACE" \
  --image-backend gemini \
  --image-count 4 \
  --image-style pencil-doodle \
  --deck-count 0 \
  --style plain 2>&1)"
CODE=$?
set -e

echo "$RESULT"
if [[ $CODE -ne 0 ]]; then
  fail_exit "执行失败，请把上面错误信息发我"
fi

OUT_PATH="$(python3 - <<'PY' "$RESULT"
import json,sys,re
s=sys.argv[1]
m=re.search(r'\{[\s\S]*\}\s*$',s)
if not m:
    print("")
    raise SystemExit(0)
try:
    d=json.loads(m.group(0))
    print(d.get("output",""))
except Exception:
    print("")
PY
)"

if [[ -n "$OUT_PATH" && -f "$OUT_PATH" ]]; then
  echo "\n已生成：$OUT_PATH"
  open -R "$OUT_PATH" 2>/dev/null || true
else
  fail_exit "未找到输出文件"
fi

read -r "?按回车结束..."
