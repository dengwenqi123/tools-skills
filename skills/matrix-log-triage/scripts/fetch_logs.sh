#!/usr/bin/env bash
# 并行下载 vela_search_matrix_log 返回的 FDS 日志链接。
# 用法: fetch_logs.sh <links.txt> <logs_dir>
#   links.txt 每行一个 FDS 链接:
#   https://cnbj1-fds.api.xiaomi.net/vela-crash-log/matrix-log/<date>/<pod>/console_<date>.log
# 产物: <logs_dir>/<date>/<pod>.log ; 下载失败的链接写入 <logs_dir>/failed.txt
set -euo pipefail
links="$1"
dir="$2"
mkdir -p "$dir"
: > "$dir/failed.txt"
grep -Eo 'https?://[^ ]+/matrix-log/[0-9-]+/[^/ ]+/console_[0-9-]+\.log' "$links" | sort -u |
while read -r url; do
  rest="${url#*/matrix-log/}"
  date="${rest%%/*}"
  pod="$(echo "$rest" | cut -d/ -f2)"
  mkdir -p "$dir/$date"
  echo "$url $dir/$date/$pod.log"
done | xargs -P 8 -n 2 sh -c 'curl -fsS --retry 2 --max-time 600 -o "$1" "$0" || echo "$0" >> "'"$dir"'/failed.txt"'
find "$dir" -name '*.log' -printf '%s\t%p\n' | sort -k2
if [ -s "$dir/failed.txt" ]; then
  echo "下载失败:" >&2
  cat "$dir/failed.txt" >&2
  exit 1
fi
