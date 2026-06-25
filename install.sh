#!/bin/sh
# install.sh — ultra-ai を導入する(macOS 前提)。依存を確認し、bin/ を PATH へ冪等追記する。
#
# 方針: 破壊的操作はしない・インストールを強制しない(不足は手順を案内するだけ)・冪等
# (二度流しても重複追記しない)。追記ブロックはマーカーで囲み、後で手で消せる。
# version 比較は bin/_lib.sh の check_version を再利用する(重複実装しない)。

set -eu

# --- repo ルートを解決(このスクリプトの場所・symlink も追う) ---
self=$0
while [ -h "$self" ]; do
  d=$(cd -P "$(dirname "$self")" && pwd)
  self=$(readlink "$self")
  case $self in /*) ;; *) self=$d/$self ;; esac
done
REPO=$(cd -P "$(dirname "$self")" && pwd)
ULTRA_BIN="$REPO/bin"

# _lib.sh を source(関数定義のみ・実害なし)。check_version を再利用する。
. "$ULTRA_BIN/_lib.sh"

ok=1  # 必須依存に欠けがあれば 0(最後にまとめて exit)

printf '\033[1m  ⊔ ultra-ai インストーラ\033[0m\n\n'

# --- macOS 確認(警告のみ・コア検証ループは他環境でも動く) ---
if [ "$(uname)" != "Darwin" ]; then
  printf '\033[33m! ultra-ai は macOS 前提です(現在: %s)。認証/通知/画像バナーは macOS 機能で、\n' "$(uname)"
  printf '  非対応環境では該当機能のみ no-op になります(検証ループ等のコアは動作)。\033[0m\n'
fi

# --- claude CLI(必須) ---
if command -v claude >/dev/null 2>&1; then
  printf '\033[32m✓\033[0m claude: %s\n' "$(claude --version 2>/dev/null | head -1)"
  check_version  # 最小 2.1.0 未満なら警告(_lib.sh・起動はブロックしない)
else
  ok=0
  printf '\033[31m✗ claude が PATH にありません(必須)。\033[0m\n'
  printf '    curl -fsSL https://claude.ai/install.sh | bash\n'
  printf '    または npm install -g @anthropic-ai/claude-code\n'
fi

# --- Python 3.11+(必須・hook が標準ライブラリ tomllib を使う) ---
py=""
for c in python3 python3.13 python3.12 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then py=$c; break; fi
done
if [ -n "$py" ] && "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  printf '\033[32m✓\033[0m python: %s\n' "$("$py" --version 2>&1)"
else
  ok=0
  printf '\033[31m✗ Python 3.11+ が必要です(hook が tomllib を使用)。\033[0m\n'
  printf '    brew install python@3.11\n'
fi

# --- 任意ツール(無くても degrade して動く) ---
for opt in chafa terminal-notifier; do
  if command -v "$opt" >/dev/null 2>&1; then
    printf '\033[32m✓\033[0m %s(任意)\n' "$opt"
  else
    printf '\033[2m·\033[0m %s 無し(任意・banner/通知が簡易表示へ degrade)→ brew install %s\n' "$opt" "$opt"
  fi
done

if [ "$ok" != 1 ]; then
  printf '\n\033[31m上記の必須依存を入れてから再実行してください。\033[0m\n'
  exit 1
fi

# --- bin/ を PATH へ冪等追記(ガード付き・後で消せる) ---
rc="$HOME/.zshrc"
case "${SHELL:-}" in *bash) rc="$HOME/.bashrc" ;; esac
marker="# >>> ultra-ai >>>"
if [ -f "$rc" ] && grep -qF "$marker" "$rc"; then
  printf '\033[32m✓\033[0m PATH は既に %s に追記済み(スキップ)\n' "$rc"
else
  {
    printf '\n%s\n' "$marker"
    printf 'export PATH="%s:$PATH"\n' "$ULTRA_BIN"
    printf '# <<< ultra-ai <<<\n'
  } >> "$rc"
  printf '\033[32m✓\033[0m PATH を %s に追記しました\n' "$rc"
fi

# --- 完了 + 次の一歩 ---
printf '\n\033[1m✓ ultra-ai セットアップ完了\033[0m\n'
printf '  反映:  source %s   (または新しいシェルを開く)\n' "$rc"
printf '  起動:  ultra-ai        # カレントディレクトリで起動\n'
printf '         uai             # 短縮\n'
printf '         ultra-ai-safe   # 全 hook 無効(壊れた hook からの退避用)\n'
