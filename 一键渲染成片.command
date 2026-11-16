#!/bin/zsh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON_BIN:-python3}"
OUT="$DIR/output/《今日AI大事件》成片.mp4"

echo "=== [1/3] 导出并检查 Google Flow 安全提示词 ==="
"$PY" "$DIR/fable_studio.py" flow-plan --project "$DIR/project.json" --output "$DIR/output/flow_tasks.json"

echo "=== [2/3] 开始合成 Fable 5.2 动效与混音 ==="
"$PY" "$DIR/fable_studio.py" render --project "$DIR/project.json" --output "$OUT"

echo "=== [3/3] 校验音画流并生成 9 宫格预览 ==="
"$PY" "$DIR/fable_studio.py" verify --video "$OUT" --output "$DIR/output/verify_grid.jpg"

echo "完成：$OUT"
open "$DIR/output"
open "$OUT"
