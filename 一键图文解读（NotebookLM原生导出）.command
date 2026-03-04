#!/bin/zsh
set -euo pipefail
export NODE_OPTIONS="--no-deprecation"

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
PIPELINE="$WORKSPACE/scripts/final_md_pipeline.py"
IMPORTER="$WORKSPACE/scripts/import_notebooklm_slides.py"
NBLM_AUTO="$WORKSPACE/scripts/notebooklm_auto_generate.py"
VENV_PY="$WORKSPACE/.venv/bin/python"

fail_exit() {
  echo "\n失败：$1"
  read -r "?按回车结束..."
  exit 1
}

echo ""
echo "Baoyu 一键图文解读（NotebookLM全自动版）"
echo "------------------------------------------------------------"
echo "- 自动生成中文解读正文（单一 Markdown）"
echo "- 自动调用 NotebookLM 生成演示文稿图像（优先下载导出）"
echo "- 自动内嵌回同一个 Markdown"
echo "- 失败直接退出，不进入手动流程"
echo "------------------------------------------------------------"

if ! command -v python3 >/dev/null 2>&1; then
  fail_exit "缺少 python3"
fi
if [[ ! -x "$VENV_PY" ]]; then
  fail_exit "缺少虚拟环境 Python：$VENV_PY"
fi
if [[ ! -f "$NBLM_AUTO" ]]; then
  fail_exit "缺少脚本：$NBLM_AUTO"
fi

read -r "?请输入链接(URL): " URL
if [[ -z "$URL" || ! "$URL" =~ ^https?:// ]]; then
  fail_exit "URL 不合法"
fi

# 正文只走文字，不走 Gemini 图片。
echo "\n[1/3] 生成正文..."
set +e
RESULT="$(python3 "$PIPELINE" \
  --url "$URL" \
  --workspace "$WORKSPACE" \
  --image-backend api \
  --image-count 0 \
  --deck-count 0 \
  --style plain 2>&1)"
CODE=$?
set -e

echo "$RESULT"
if [[ $CODE -ne 0 ]]; then
  fail_exit "正文生成失败"
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
[[ -n "$OUT_PATH" && -f "$OUT_PATH" ]] || fail_exit "未找到输出 Markdown"

echo "\n[2/3] 自动调用 NotebookLM..."
AUTO_DIR="$WORKSPACE/.tmp-notebooklm"
rm -rf "$AUTO_DIR"
mkdir -p "$AUTO_DIR"

set +e
AUTO_JSON="$($VENV_PY "$NBLM_AUTO" --url "$URL" --out-dir "$AUTO_DIR" --count 3 2>&1)"
AUTO_CODE=$?
set -e

echo "$AUTO_JSON"
if [[ $AUTO_CODE -ne 0 ]]; then
  if [[ "$AUTO_JSON" == *"NotebookLM profile is not signed in"* ]]; then
    echo "检测到专用 Profile 未登录。请先执行一次："
    echo "open -na \"Google Chrome\" --args --user-data-dir=\"$HOME/Library/Application Support/BaoyuNotebookLM\" --profile-directory=Default https://notebooklm.google.com"
    echo "登录成功后，关闭该窗口，再重跑本脚本。"
  fi
  fail_exit "NotebookLM 自动生成失败"
fi

echo "\n[3/3] 回填到 Markdown..."
python3 "$IMPORTER" --md "$OUT_PATH" --slides "$AUTO_DIR" --max 3 >/tmp/baoyu_nblm_import.log 2>&1 || {
  cat /tmp/baoyu_nblm_import.log
  fail_exit "NotebookLM 图片回填失败"
}
cat /tmp/baoyu_nblm_import.log

echo "\n完成：$OUT_PATH"
open -R "$OUT_PATH" 2>/dev/null || true
read -r "?按回车结束..."
