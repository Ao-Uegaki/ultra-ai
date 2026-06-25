#!/usr/bin/env python3
"""ua_audit.py — ultra-ai 自身の設定面の on-demand 自己監査(/ua-check から起動)。

ultra-ai は hook / シェルコマンド / MCP を実行し transcript を読むのに、**自分自身の**
config / hooks / agents / skills / MCP は監査してこなかった。ECC の AgentShield の
5系統(secrets / permissions / hook-injection / MCP / agent-config)を**要点圧縮**し、
~十数個の critical/high チェックだけを決定論で回す(ルールはここに直書き=`rules/` dir は作らない)。

設計:
  - 監査するのは**キュレートされた設定面のみ**(settings*.json, CLAUDE.md, hooks/, agents/,
    skills/, commands/, workflows/, MCP 設定)。state/ plugins/ cache/ projects/ 等の runtime は見ない。
  - 三状態: 何か見つかれば FAIL、検査不能(例: settings.json が壊れている)は **UNKNOWN**
    (黙って PASS にしない=gate.py と同じ哲学)、問題なしで PASS。
  - on-demand 専用(Stop では走らせない=fast lane と cache を汚さない)。read-only。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

CRITICAL, HIGH = "CRITICAL", "HIGH"

# 内容スキャン対象の単体ファイル(.claude.json は巨大な runtime state なので内容は見ない)。
CONTENT_FILES = ("settings.json", "settings.local.json", "CLAUDE.md", ".mcp.json")
SCAN_DIRS = ("hooks", "agents", "skills", "commands", "workflows", "rules")
TEXT_EXTS = {".py", ".md", ".json", ".toml", ".js", ".sh", ".txt"}

# hook コマンドの注入/実行ベクトル: コマンド置換 $( … )、バッククォート、シェルへのパイプ、eval。
_HOOK_DANGER = re.compile(r"\$\(|`|\|\s*(?:sudo\s+)?(?:ba)?sh\b|\beval\b")
# hooks/agents/skills/commands の .py に潜む未サニタイズな実行(各 needle は \. と \( で
# エスケープしてあるので、この監査スクリプト自身のソースには self-match しない)。
# メッセージ文字列はトリガ token の直後に `(` を置かない(この監査が自分のソースを self-match しないため)。
_CODE_DANGER = [
    (re.compile(r"\bos\.system\s*\("), "os.system による未サニタイズなシェル実行"),
    (re.compile(r"(?<![\w.])eval\s*\("), "eval による動的実行"),
    (re.compile(r"(?<![\w.])exec\s*\("), "exec による動的実行"),
]


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None


def _text_files(root: Path) -> list[Path]:
    files = [root / n for n in CONTENT_FILES if (root / n).is_file()]
    for d in SCAN_DIRS:
        base = root / d
        if base.is_dir():
            files += [p for p in sorted(base.rglob("*"))
                      if p.is_file() and p.suffix in TEXT_EXTS]
    return files


def scan_content(root: Path, files: list[Path]) -> list[tuple]:
    """ハードコードされた機密 + 不可視/双方向の隠し指示文字。"""
    out = []
    for p in files:
        txt = _read(p)
        if txt is None:
            continue
        rel = str(p.relative_to(root))
        for pat, label in common.SECRET_CONTENT_PATTERNS:
            if pat.search(txt):
                out.append((CRITICAL, rel, f"ハードコードされた機密の可能性: {label}"))
                break  # 1ファイル1件で十分
        if common.HIDDEN_UNICODE.search(txt):
            out.append((CRITICAL, rel, "不可視/双方向の制御文字(隠し指示の注入面)"))
    return out


def scan_code(root: Path) -> list[tuple]:
    out = []
    for d in ("hooks", "agents", "skills", "commands"):
        base = root / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            txt = _read(p)
            if txt is None:
                continue
            rel = str(p.relative_to(root))
            for pat, msg in _CODE_DANGER:
                if pat.search(txt):
                    out.append((HIGH, rel, msg))
    return out


def scan_secret_files(root: Path) -> list[tuple]:
    """設定面に紛れた機密*ファイル*(.env/.pem/.key/id_rsa 等)。top-level + SCAN_DIRS のみ。"""
    candidates = [p for p in root.iterdir() if p.is_file()]
    for d in SCAN_DIRS:
        base = root / d
        if base.is_dir():
            candidates += [p for p in base.rglob("*") if p.is_file()]
    return [(HIGH, str(p.relative_to(root)), "機密ファイルが設定面に存在(.env/.pem/.key 等)")
            for p in candidates if common.looks_secret_file(str(p))]


def check_permissions(obj: dict, cfg: str) -> list[tuple]:
    out = []
    perms = obj.get("permissions") or {}
    for a in (perms.get("allow") or []):
        if not isinstance(a, str):
            continue
        s = a.replace(" ", "")
        if s in ("*", "Bash", "Bash(*)") or s.endswith("(*)"):
            out.append((HIGH, cfg, f"過剰に広い権限付与: allow \"{a}\""))
    if obj.get("disableAllHooks") is True:
        out.append((HIGH, cfg, "disableAllHooks: true が永続設定にある(安全網を無効化)"))
    return out


def check_hooks(obj: dict, cfg: str) -> list[tuple]:
    out = []
    for event, groups in (obj.get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue
        for g in groups:
            for h in (g.get("hooks") or []):
                cmd = h.get("command")
                if isinstance(cmd, str) and _HOOK_DANGER.search(cmd):
                    out.append((HIGH, cfg, f"hook コマンドに注入/実行ベクトル: {event} → {cmd[:80]}"))
    return out


def check_mcp(obj: dict, cfg: str) -> list[tuple]:
    out = []
    servers = obj.get("mcpServers")
    if not isinstance(servers, dict):
        return out
    for name, sc in servers.items():
        if not isinstance(sc, dict):
            continue
        argstr = " ".join(str(a) for a in (sc.get("args") or []))
        blob = f"{sc.get('command') or ''} {argstr}"
        if re.search(r"\bnpx\b[^\n]*?(?:\s-y\b|\s--yes\b)", blob) or re.search(r"\buvx\b", blob):
            out.append((HIGH, cfg, f"MCP \"{name}\": npx -y/uvx 自動インストール(供給網リスク)"))
        if sc.get("url") or sc.get("type") in ("http", "sse"):
            out.append((HIGH, cfg, f"MCP \"{name}\": リモートトランスポート(url/{sc.get('type')})"))
    return out


def _is_agent_def(txt: str) -> bool:
    """frontmatter に name: を持つ = Claude Code の subagent 定義(README/snippet は除外)。"""
    if not txt.lstrip().startswith("---"):
        return False
    parts = txt.split("---", 2)
    return len(parts) >= 3 and re.search(r"(?m)^\s*name:\s*\S", parts[1]) is not None


def check_agent_baseline(root: Path) -> list[tuple]:
    """全 subagent 定義(agents/*.md の name: 付き)に防御 baseline が焼き込まれているか。
    欠落/改変は prompt-injection 防御の drift(common.AGENT_DEFENSE_BASELINE と突合)。"""
    out = []
    base = root / "agents"
    if not base.is_dir():
        return out
    for p in sorted(base.glob("*.md")):
        txt = _read(p)
        if txt is None or not _is_agent_def(txt):
            continue
        if common.AGENT_DEFENSE_BASELINE not in txt:
            out.append((HIGH, str(p.relative_to(root)),
                        "防御 baseline が未注入/改変(prompt-injection 防御の drift)"))
    return out


def audit(root: Path) -> dict:
    """設定面を監査し {overall, findings, unknown} を返す(純粋・テスト可能)。"""
    root = Path(root)
    findings: list[tuple] = []
    unknown: list[str] = []

    files = _text_files(root)
    findings += scan_content(root, files)
    findings += scan_code(root)
    findings += scan_secret_files(root)
    findings += check_agent_baseline(root)

    for cfg in ("settings.json", "settings.local.json"):
        p = root / cfg
        if not p.is_file():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            unknown.append(f"{cfg}: JSON 解析に失敗 — 権限/フック/MCP を検査できません")
            continue
        findings += check_permissions(obj, cfg)
        findings += check_hooks(obj, cfg)
        findings += check_mcp(obj, cfg)

    for cfg in (".mcp.json", ".claude.json"):
        p = root / cfg
        if not p.is_file():
            continue
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            if cfg == ".mcp.json":
                unknown.append(f"{cfg}: JSON 解析に失敗 — MCP を検査できません")
            continue
        findings += check_mcp(obj, cfg)

    findings = sorted(set(findings))
    overall = common.FAIL if findings else (common.UNKNOWN if unknown else common.PASS)
    return {"overall": overall, "findings": findings, "unknown": unknown}


def format_report(res: dict) -> str:
    lines = [f"ua-audit: {res['overall']}"]
    for sev, rel, msg in res["findings"]:
        lines.append(f"  [{sev}] {rel}: {msg}")
    for u in res["unknown"]:
        lines.append(f"  [UNKNOWN] {u}")
    if res["overall"] == common.PASS:
        lines.append("  クリーン: 設定面に critical/high の問題は検出されませんでした。")
    elif res["overall"] == common.UNKNOWN and not res["findings"]:
        lines.append("  一部を検査できませんでした(未検証=PASS ではない)。上記を解消して再実行してください。")
    return "\n".join(lines)


def main() -> int:
    res = audit(common.config_dir())
    print(format_report(res))
    return {common.PASS: 0, common.FAIL: 1, common.UNKNOWN: 0}[res["overall"]]


if __name__ == "__main__":
    sys.exit(main())
