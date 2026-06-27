#!/bin/sh
# _lib.sh — ultra-ai ランチャ共通ライブラリ(ultra-ai / ultra-ai-safe が source する)。
#
# 呼び出し側が解決した「実 bin ディレクトリ」($ULTRA_BIN)から設定ルートを導出するので、
# clone / symlink した場所に依存しない(絶対パスのハードコードなし)。
# claude-home が CLAUDE_CONFIG_DIR(settings.json・CLAUDE.md・agents/・skills/・hooks)になる。
# サブスク OAuth は macOS Keychain(設定ディレクトリ外)にあるため、設定ルートを切り替えても
# サブスク認証は維持される — API キーは不要。

ULTRA_ROOT=$(dirname "$ULTRA_BIN")
export CLAUDE_CONFIG_DIR="$ULTRA_ROOT/claude-home"

# 'claude' が PATH に無ければ即終了(127)。${0##*/} は呼び出し元ランチャ名。
require_claude() {
  if ! command -v claude >/dev/null 2>&1; then
    echo "${0##*/}: error: 'claude' not found on PATH" >&2
    exit 127
  fi
}

# 最小バージョン確認(warn-only・起動は決してブロックしない)。
check_version() {
  MIN_CC="2.1.0"
  have=$(claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  if [ -n "$have" ] && [ "$(printf '%s\n%s\n' "$MIN_CC" "$have" | sort -V | head -1)" != "$MIN_CC" ]; then
    echo "${0##*/}: warning: claude $have < $MIN_CC (some features may not work)" >&2
  fi
}

# テキスト/ANSI のロゴ(全端末で動くフォールバック)。ロゴ ultra-ai.png 準拠の
# ⊔ モノグラム + `ultra-`(太字)/`ai`(青)のツートーン。
ua_banner_text() {
  printf '\n\033[1m  ⊔ ultra-\033[38;2;46;124;247mai\033[0m\n\033[2m  self-contained Claude Code\033[0m\n\n'
}

# 起動バナー: 対応端末では実ロゴ画像、非対応端末はテキストにフォールバック。
# CC のバナーは抑制/置換できない(ハードコード)ため、これはその「上」に1回出る。
# UA_BANNER=0 で無効(停止スイッチ)。非 tty / print モードでは出さない。
#
# 処理の流れ(何を・なぜ):
#   1) 早期 return: 無効/非 tty/print モード/ロゴ無し なら出さない(またはテキストへ)。
#   2) フォーマット決定: 端末の種類($KITTY_WINDOW_ID/$TERM_PROGRAM)から出力形式(kitty/iterm/sixels/symbols)を選ぶ。
#   3) レンダラ選定: まず移植性の高い chafa、無ければ端末ネイティブの手段、最後はテキストにフォールバック。
ua_banner() {
  [ "${UA_BANNER:-1}" = "0" ] && return 0
  [ -t 1 ] || return 0
  for a in "$@"; do
    case "$a" in -p | --print) return 0 ;; esac
  done
  logo="$ULTRA_ROOT/ultra-ai.png"
  [ -f "$logo" ] || { ua_banner_text; return 0; }
  # [2] 端末ごとに出力フォーマットを決定(UA_BANNER_FORMAT で明示上書き可)。
  # 実画像が出せる端末は kitty/iterm/sixels、それ以外は崩れないブロック文字モザイク。
  # sixels=対応端末で画像を出すための画像形式 / symbols=画像をブロック文字で近似(全端末で崩れない)。
  fmt="${UA_BANNER_FORMAT:-}"
  if [ -z "$fmt" ]; then
    if [ -n "$KITTY_WINDOW_ID" ]; then
      fmt=kitty
    else
      case "$TERM_PROGRAM" in
        iTerm.app) fmt=iterm ;;
        WezTerm)   fmt=iterm ;;
        vscode)    fmt=sixels ;;   # 要 VS Code 画像有効化(enableImages + gpu≠off)
        *)         fmt=symbols ;;
      esac
    fi
  fi
  # [3] レンダラ選定。chafa(画像を文字/sixel 等に変換して端末に出すツール)。
  # chafa: 最も移植性が高いレンダラ。symbols は既定だと細い罫線で化けるため
  # ブロック文字だけに絞る(全端末・全フォントで崩れない)。実画像は端末対応時のみ。
  if command -v chafa >/dev/null 2>&1; then
    if [ "$fmt" = symbols ]; then
      chafa -f symbols --symbols block+space --size "${UA_BANNER_SIZE:-24x12}" "$logo" && return 0
    else
      qual=""
      [ "$fmt" = sixels ] && qual="--dither none --work 9"  # 斑点除去 + 解析を最大努力(sixel のみ・truecolor 経路は素のまま)
      chafa -f "$fmt" $qual --size "${UA_BANNER_SIZE:-24x12}" "$logo" && return 0
    fi
  fi
  # chafa 不在時のネイティブ手段(あれば)→ 最後はテキスト。
  # 以下はいずれも「その端末に画像を直接出す」専用ツール:
  #   kitten icat=kitty / imgcat=iTerm / wezterm imgcat=WezTerm / img2sixel=画像を sixel に変換して出す。
  if [ -n "$KITTY_WINDOW_ID" ] && command -v kitten >/dev/null 2>&1; then
    kitten icat --align left "$logo" 2>/dev/null && return 0
  fi
  case "$TERM_PROGRAM" in
    iTerm.app) command -v imgcat  >/dev/null 2>&1 && imgcat -H 8 "$logo"   && return 0 ;;
    WezTerm)   command -v wezterm >/dev/null 2>&1 && wezterm imgcat "$logo" && return 0 ;;
  esac
  command -v img2sixel >/dev/null 2>&1 && img2sixel -w 220 "$logo" 2>/dev/null && return 0
  ua_banner_text
}

# 自前の通知アプリを生成(通知の左アイコン=ultra-ai ロゴ)。macOS は通知の左アイコンを
# 「投稿したアプリのアイコン」で固定し、per-通知の -appIcon は Sonoma で無視される。素の
# terminal-notifier は古い未署名バンドルでアイコンが解決できず灰色になるため、その .app を
# 複製してアイコンを ultra-ai ロゴ・bundle id を dev.ultra-ai.notifier に差し替えた自前バンドルを
# 作る(notify.py がこのバイナリを優先して呼ぶ)。冪等(ロゴ未変更ならスキップ)・失敗は黙って
# 素通り(起動を絶対ブロックしない)。UA_NOTIFY_APP=0 で無効=停止スイッチ。生成物は
# claude-home/state/(gitignore・per-machine)に置き、削除で完全に元へ戻る。
#
# 処理の流れ(何を・なぜ):
#   1) 前提チェック: macOS か / ロゴ有り / 必要ツール(terminal-notifier・sips・iconutil)有り。無ければ素通り。
#   2) 冪等判定: ロゴの sha が前回と同じで .app も在るならスキップ。
#   3) 元 .app を解決: terminal-notifier の bin から本体 .app を辿る(2行ラッパ/symlink に対応)。
#   4) 複製→アイコン差し替え: tmp に複製し、PNG をロゴ .icns に変換して入れ替え、Info.plist を書き換える。
#   5) 署名し直し→原子差し替え→Launch Services 登録: 改変で無効化した署名を ad-hoc で署名し直して反映。
# 外部ツール: sips=画像を各サイズに縮小 / iconutil=PNG群→.icns に変換 / PlistBuddy=Info.plist を編集 / codesign=ad-hoc 署名 / lsregister=Launch Services にアイコンを登録。
ua_notifier_ensure() {
  [ "${UA_NOTIFY_APP:-1}" = "0" ] && return 0
  [ "$(uname)" = "Darwin" ] || return 0
  un_logo="$ULTRA_ROOT/ultra-ai.png"
  [ -f "$un_logo" ] || return 0
  command -v terminal-notifier >/dev/null 2>&1 || return 0
  command -v sips >/dev/null 2>&1 || return 0
  command -v iconutil >/dev/null 2>&1 || return 0

  un_dir="$CLAUDE_CONFIG_DIR/state/notifier"
  un_dest="$un_dir/ultra-ai-notifier.app"
  un_stamp="$un_dir/.logo.sha"
  un_cur=$(shasum -a 256 "$un_logo" 2>/dev/null | awk '{print $1}')
  [ -n "$un_cur" ] || return 0
  # 冪等: 既に在って同じロゴから作ったならスキップ。
  if [ -d "$un_dest" ] && [ "$(cat "$un_stamp" 2>/dev/null)" = "$un_cur" ]; then
    return 0
  fi

  # 元 .app を解決(Homebrew の bin は .app/Contents/MacOS を exec する2行ラッパ。symlink も追う)。
  un_tn=$(command -v terminal-notifier) || return 0
  while [ -h "$un_tn" ]; do
    un_d=$(cd -P "$(dirname "$un_tn")" && pwd)
    un_l=$(readlink "$un_tn")
    case "$un_l" in /*) un_tn="$un_l" ;; *) un_tn="$un_d/$un_l" ;; esac
  done
  case "$un_tn" in
    */Contents/MacOS/*) un_src="${un_tn%/Contents/MacOS/*}" ;;
    *) un_src=$(awk -F'"' '/Contents\/MacOS/{print $2; exit}' "$un_tn" 2>/dev/null); un_src="${un_src%/Contents/MacOS/*}" ;;
  esac
  [ -n "$un_src" ] && [ -d "$un_src/Contents" ] || return 0

  # 複製は tmp に作って最後に原子的に差し替える(中途半端な .app を notify.py に見せない)。
  mkdir -p "$un_dir" || return 0
  un_tmp="$un_dir/.build.$$"
  rm -rf "$un_tmp"
  cp -R "$un_src" "$un_tmp" 2>/dev/null || { rm -rf "$un_tmp"; return 0; }

  # PNG → .icns(標準の sips + iconutil。1254px 原画から各サイズへ縮小)。
  un_work=$(mktemp -d) || { rm -rf "$un_tmp"; return 0; }
  un_iconset="$un_work/icon.iconset"
  mkdir -p "$un_iconset"
  for un_s in 16 32 128 256 512; do
    sips -z "$un_s" "$un_s" "$un_logo" --out "$un_iconset/icon_${un_s}x${un_s}.png" >/dev/null 2>&1
    un_s2=$((un_s * 2))
    sips -z "$un_s2" "$un_s2" "$un_logo" --out "$un_iconset/icon_${un_s}x${un_s}@2x.png" >/dev/null 2>&1
  done
  iconutil -c icns "$un_iconset" -o "$un_tmp/Contents/Resources/AppIcon.icns" >/dev/null 2>&1 \
    || { rm -rf "$un_work" "$un_tmp"; return 0; }
  rm -rf "$un_work"
  rm -f "$un_tmp/Contents/Resources/Terminal.icns"

  # Info.plist: アイコン名・bundle id(新規アプリ扱い)・表示名を差し替える。
  un_pl="$un_tmp/Contents/Info.plist"
  un_pb=/usr/libexec/PlistBuddy
  "$un_pb" -c "Set :CFBundleIconFile AppIcon" "$un_pl" 2>/dev/null \
    || "$un_pb" -c "Add :CFBundleIconFile string AppIcon" "$un_pl" 2>/dev/null
  "$un_pb" -c "Set :CFBundleIdentifier dev.ultra-ai.notifier" "$un_pl" 2>/dev/null \
    || "$un_pb" -c "Add :CFBundleIdentifier string dev.ultra-ai.notifier" "$un_pl" 2>/dev/null
  "$un_pb" -c "Set :CFBundleName ultra-ai" "$un_pl" 2>/dev/null

  # adhoc 署名し直し(plist/Resources 改変で元署名は無効になるため)→ 原子差し替え →
  # Launch Services 登録 + touch でアイコンキャッシュを更新。
  codesign --force --deep --sign - "$un_tmp" >/dev/null 2>&1
  rm -rf "$un_dest"
  mv "$un_tmp" "$un_dest" 2>/dev/null || { rm -rf "$un_tmp"; return 0; }
  un_lsr=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
  [ -x "$un_lsr" ] && "$un_lsr" -f "$un_dest" >/dev/null 2>&1
  touch "$un_dest" 2>/dev/null

  printf '%s\n' "$un_cur" > "$un_stamp" 2>/dev/null
  return 0
}

# claude を起動(ultracode を既定 ON で注入)。$1=セッション名・以降は claude へ転送。
# settings.json は effortLevel に ultracode を受け付けない(session-only)ため、launcher の
# --settings インライン JSON で注入する。--settings は既存設定への上書き合成なので
# model / hooks / effortLevel:xhigh は維持され、ultracode(= xhigh + 自動 workflow orchestration)が上乗せされる。
# UA_ULTRACODE=0/off/false/no で無効=停止スイッチ → 注入せず素の xhigh(effortLevel)に縮退。
ua_exec_claude() {
  name=$1
  shift
  case "${UA_ULTRACODE:-1}" in
    0 | off | false | no | OFF | FALSE | NO)
      exec claude --name "$name" "$@"
      ;;
    *)
      exec claude --settings '{"ultracode":true}' --name "$name" "$@"
      ;;
  esac
}
