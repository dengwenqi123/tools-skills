#!/usr/bin/env bash
# 把 tools-skills 同步给 Kiro 和 Claude（幂等，可反复执行）：
#   skills/*        -> ~/.kiro/skills/、~/.claude/skills/（软链，渐进式加载）
#   knowledge/INDEX -> 把 {{ROOT}} 换成本机路径后渲染到 ~/.kiro/steering/tools-skills.md（常驻）
#                   -> ~/.claude/CLAUDE.md 里追加 @import 该渲染文件（常驻）
# 改了 INDEX.md 需要重跑本脚本
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
INDEX="$HOME/.kiro/steering/tools-skills.md"

for dst in "$HOME/.kiro/skills" "$HOME/.claude/skills"; do
  mkdir -p "$dst"
  # 清掉指向本仓库、但源已删除的悬空链接
  for l in "$dst"/*; do
    [[ -L "$l" && ! -e "$l" && "$(readlink "$l")" == "$ROOT/"* ]] && rm "$l" && echo "removed stale $l"
  done
  for src in "$ROOT"/skills/*/; do
    src="${src%/}"; name="$(basename "$src")"; t="$dst/$name"
    if [[ -e "$t" && ! -L "$t" ]]; then
      echo "SKIP $t：已存在同名实体目录，请手动处理" >&2; continue
    fi
    ln -sfn "$src" "$t"
  done
  echo "linked skills -> $dst"
done

mkdir -p "$HOME/.kiro/steering"
rm -f "$INDEX"  # 旧版是软链，先删掉，避免写穿到源文件
sed "s#{{ROOT}}#$ROOT#g" "$ROOT/knowledge/INDEX.md" > "$INDEX"
echo "rendered $INDEX"

CLAUDE_MD="$HOME/.claude/CLAUDE.md"
touch "$CLAUDE_MD"
if ! grep -qF "@$INDEX" "$CLAUDE_MD"; then
  printf '\n# tools-skills 知识索引\n@%s\n' "$INDEX" >> "$CLAUDE_MD"
  echo "appended import to $CLAUDE_MD"
fi
