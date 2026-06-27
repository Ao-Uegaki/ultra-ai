#!/usr/bin/env python3
"""push.py — /ua-ship の本体: コミット済みの変更を feature ブランチへ安全に push する。

サブコマンド:
  check               副作用なしの事前点検を JSON で出す(skill が読み、人へ日本語で提示)。
  do --remote R --branch B [--force-with-lease] [--allow-unverified]
                      hard ゲート(clean-tree / PASS 検証 / main 直拒否)を満たしたら push。

決定論・ゼロトークン。common の helper を再利用。これは skill が明示起動するコマンド(hook ではない)。
PR 作成(gh pr create)は skill 側で本文を整えて行う(body 末尾に Claude Code の footer)。
`UA_SHIP=0` で無効・`UA_SHIP_ALLOW_MAIN=1` で main 直 push を許可(既定は拒否)。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")


def _arg(args: list[str], flag: str) -> str | None:
    for i, a in enumerate(args):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a[len(flag) + 1:]
    return None


# ----------------------------------------------------- git/gh (best-effort) ---

def is_clean(cwd: str) -> bool:
    return not common.git_status_porcelain(cwd)


def upstream(cwd: str) -> str | None:
    cp = common.run_git(["git", "rev-parse", "--abbrev-ref",
                         "--symbolic-full-name", "@{u}"], cwd)
    return cp.stdout.strip() if cp and cp.returncode == 0 and cp.stdout.strip() else None


def ahead_behind(cwd: str, up: str | None) -> tuple[int, int]:
    if not up:
        return 0, 0
    cp = common.run_git(["git", "rev-list", "--left-right", "--count",
                         f"{up}...HEAD"], cwd)
    if cp and cp.returncode == 0 and len(cp.stdout.split()) == 2:
        behind, ahead = cp.stdout.split()
        return int(ahead), int(behind)
    return 0, 0


def commits_to_push(cwd: str, up: str | None) -> list[str]:
    rng = f"{up}..HEAD" if up else "HEAD"
    cp = common.run_git(["git", "log", "--format=%h %s", rng], cwd)
    return [ln for ln in cp.stdout.splitlines() if ln] if cp and cp.returncode == 0 else []


def author_emails(cwd: str, up: str | None) -> list[str]:
    rng = f"{up}..HEAD" if up else "HEAD"
    cp = common.run_git(["git", "log", "--format=%ae%n%ce", rng], cwd)
    if not (cp and cp.returncode == 0):
        return []
    out: list[str] = []
    for e in cp.stdout.split():
        if e and e not in out:
            out.append(e)
    return out


def remote_url(cwd: str, remote: str) -> str | None:
    cp = common.run_git(["git", "remote", "get-url", remote], cwd)
    return cp.stdout.strip() if cp and cp.returncode == 0 and cp.stdout.strip() else None


def gh_visibility(cwd: str) -> str | None:
    """公開リポの visibility(PUBLIC/PRIVATE/INTERNAL)を gh で best-effort 取得(無ければ None)。"""
    cp = common.run_git(["gh", "repo", "view", "--json", "visibility",
                         "-q", ".visibility"], cwd)
    return cp.stdout.strip() if cp and cp.returncode == 0 and cp.stdout.strip() else None


def license_present(cwd: str) -> bool:
    root = Path(common.project_root(cwd))
    return any((root / n).exists() for n in LICENSE_NAMES)


def exposed_secrets(cwd: str) -> list[str]:
    cp = common.run_git(["git", "ls-files", "-z"], cwd)
    if not (cp and cp.returncode == 0):
        return []
    return [f for f in cp.stdout.split("\0") if f and common.looks_secret_file(f)]


def verified_head(cwd: str) -> str | None:
    return common.read_json(
        common.shared_state_dir(cwd) / common.STATE_VERIFIED_HEAD).get("head")


def pass_gate_ok(cwd: str) -> bool:
    """push 対象の tip が /ua-checkpoint の PASS ゲートを通った HEAD と一致するか。"""
    vh = verified_head(cwd)
    return bool(vh) and vh == common.git_head(cwd)


# ------------------------------------------------------------ pure decision ---

def do_block_reason(branch: str | None, clean: bool, pass_ok: bool,
                    allow_main: bool, allow_unverified: bool) -> str | None:
    """do の hard ゲート(純関数・テスト可)。push 可なら None、止めるなら理由を返す。"""
    if not clean:
        return "未コミットの変更があります。先に /ua-checkpoint してコミットしてください。"
    if branch in ("main", "master") and not allow_main:
        return ("main/master へ直接 push しようとしています。feature ブランチ + PR を使ってください"
                "(意図的なら UA_SHIP_ALLOW_MAIN=1)。")
    if not pass_ok and not allow_unverified:
        return ("push 対象の HEAD が PASS 検証済みではありません(/ua-checkpoint の PASS ゲートを"
                "通っていない)。検証して checkpoint してから、または --allow-unverified で。")
    return None


# ------------------------------------------------------------------- report ---

def build_report(cwd: str, remote: str, branch: str | None) -> dict:
    up = upstream(cwd)
    ahead, behind = ahead_behind(cwd, up)
    local = common.git_branch(cwd)
    target = branch or (up.split("/", 1)[1] if up and "/" in up else local)
    vis = gh_visibility(cwd)
    report = {
        "committed": is_clean(cwd),
        "uncommitted": common.git_status_porcelain(cwd)[:20],
        "local_branch": local,
        "upstream": up,
        "ahead": ahead,
        "behind": behind,
        "target": {"remote": remote, "branch": target, "url": remote_url(cwd, remote)},
        "visibility": vis,
        "is_main_target": target in ("main", "master"),
        "head": common.git_head(cwd),
        "pass_gate": pass_gate_ok(cwd),
        "verified_head": verified_head(cwd),
        "commits_to_push": commits_to_push(cwd, up),
    }
    if vis == "PUBLIC":
        report["public_checks"] = {
            "license_present": license_present(cwd),
            "exposed_secrets": exposed_secrets(cwd),
            "author_emails": author_emails(cwd, up),
        }
    return report


def cmd_do(cwd: str, args: list[str]) -> int:
    remote = _arg(args, "--remote") or "origin"
    branch = _arg(args, "--branch") or common.git_branch(cwd)
    force = "--force-with-lease" in args
    allow_main = common.flag_enabled("SHIP_ALLOW_MAIN", default=False)
    allow_unverified = "--allow-unverified" in args
    reason = do_block_reason(branch, is_clean(cwd), pass_gate_ok(cwd),
                             allow_main, allow_unverified)
    if reason:
        print("ua-ship: 拒否 — " + reason)
        return 1
    local = common.git_branch(cwd)
    cmd = ["git", "push"]
    if force:
        cmd.append("--force-with-lease")
    cmd += [remote, f"{local}:{branch}"]
    cp = common.run_git(cmd, cwd)
    if not (cp and cp.returncode == 0):
        print("ua-ship: push に失敗しました:\n"
              + ((cp.stderr or cp.stdout)[:500] if cp else "(git timed out)"))
        return 1
    print(f"ua-ship: {local} を {remote}/{branch} に push しました。")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: push.py {check|do} [--remote R --branch B ...]")
        return 1
    if not common.flag_enabled("SHIP"):
        print("ua-ship: UA_SHIP=0 で無効です。")
        return 0
    cwd = os.getcwd()
    if not common.is_git_repo(cwd):
        print("ua-ship: git リポジトリではありません。")
        return 1
    sub = args[0]
    if sub == "check":
        remote = _arg(args, "--remote") or "origin"
        print(json.dumps(build_report(cwd, remote, _arg(args, "--branch")),
                         ensure_ascii=False, indent=2))
        return 0
    if sub == "do":
        return cmd_do(cwd, args)
    print(f"push.py: 未知のサブコマンド: {sub}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
