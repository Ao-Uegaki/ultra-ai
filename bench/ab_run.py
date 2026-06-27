#!/usr/bin/env python3
"""bench/ab_run.py — 対・素 Claude の 2アーム A/B を headless で回す runner(PoC)。

各 (task × arm × run) で: 使い捨て sandbox に task の repo をコピー → `claude -p` を起動
→ 結果 JSON から session_id を得て transcript を特定 → **held-out oracle**(採点用に隠しておくテスト)を
sandbox に入れて実行し pass/fail → 1行を results jsonl に追記。採点・集計は `ab_report.py`。

- arm: control=素 claude(`bench/arm-control-config` + UA_* 全 OFF)/ treatment=ultra-ai(`claude-home`)。
- headless で Stop hook 発火は未保証だが transcript は必ず書かれる → hook 非依存で事後採点。
- **安全**: 実行は必ず使い捨て sandbox dir のみ(本物の repo に走らせない)。ネットワークは API のみ。
- `claude -p` 呼び出し(`invoke_claude`)以外は純関数(テストは invoke を差し替える)。

使い方: `python3 bench/ab_run.py [-k 3] [--tasks t1 t2] [--arms control treatment]`
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcript_metrics  # noqa: E402  (inline metrics 抽出を ab_report と共有)

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "bench" / "tasks"
RESULTS_DIR = ROOT / "bench" / "results"
SANDBOX_ROOT = ROOT / "bench" / ".sandboxes"

ARMS = {
    # control=素の Claude: 専用の空 config dir + 学習/approach/rules を念のため全 OFF。
    # model は両アームとも opus に揃える(公平性=「同じモデルで ultra-ai の仕組み on/off」)。
    "control": {"config_dir": str(ROOT / "bench" / "arm-control-config"),
                "env": {"UA_AUTOAPPLY": "0", "UA_ROUTE": "0", "UA_RULES": "0"},
                "model": "opus"},
    # control-xhigh=control と同一の素 config を、`--effort xhigh` フラグだけ付けて回す。
    # config_dir は authed な arm-control-config を共有(別 dir の再ログイン不要)。control との
    # 差は --effort 「のみ」=effort 単独効果の厳密な切り分け。treatment vs control-xhigh=「仕組みのみ」。
    "control-xhigh": {"config_dir": str(ROOT / "bench" / "arm-control-config"),
                      "env": {"UA_AUTOAPPLY": "0", "UA_ROUTE": "0", "UA_RULES": "0"},
                      "model": "opus", "effort": "xhigh"},
    # treatment=ultra-ai 全部入り: 実際に使う claude-home そのもの(settings も opus)。
    "treatment": {"config_dir": str(ROOT / "claude-home"), "env": {}, "model": "opus"},
}

# scoped 許可ツール(`--dangerously-skip-permissions` は使わない)。sandbox 内で
# 「編集 + テスト実行」だけ可能にし、任意コマンドは不可にする。両アーム共通=公平。
# headless で非許可ツールは自動 deny(hang しない)。
ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Glob", "Grep",
    "Bash(python3:*)", "Bash(python:*)", "Bash(pytest:*)",
    "Bash(ls:*)", "Bash(cat:*)", "Bash(pwd:*)", "Bash(find:*)", "Bash(grep:*)",
]


def _build_cmd(prompt: str, model: str | None = None,
               effort: str | None = None) -> list[str]:
    """`claude -p` のコマンドを構築(純関数・テスト可能)。skip-permissions は付けない。"""
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--allowedTools", ",".join(ALLOWED_TOOLS)]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]  # control-xhigh は素 config + これだけで xhigh
    return cmd


def load_task(task_dir: str) -> dict:
    """task ディレクトリ(meta.json + prompt.md)を読む。"""
    td = Path(task_dir)
    meta = json.loads((td / "meta.json").read_text(encoding="utf-8"))
    return {"id": meta.get("id", td.name), "dir": str(td), "meta": meta,
            "prompt": (td / "prompt.md").read_text(encoding="utf-8"),
            "model": meta.get("model")}


def prepare_sandbox(task_dir: str, dest: str) -> str:
    """task の `repo/` を使い捨て sandbox にコピー(既存は消してから)。"""
    d = Path(dest)
    if d.exists():
        shutil.rmtree(d)
    shutil.copytree(Path(task_dir) / "repo", d)
    return str(d)


def run_oracle(meta: dict, sandbox: str, task_dir: str) -> bool:
    """held-out oracle を sandbox に入れて test_cmd を実行し pass/fail を返す。

    oracle は agent 実行**後**にコピーする(agent が採点テストを書き換え・gaming できないように)。
    """
    oracle = Path(task_dir) / "oracle"
    if oracle.exists():
        shutil.copytree(oracle, Path(sandbox), dirs_exist_ok=True)
    try:
        cp = subprocess.run(meta["test_cmd"], cwd=sandbox, capture_output=True,
                            text=True, timeout=meta.get("oracle_timeout", 120))
        return cp.returncode == 0
    except Exception:
        return False


def find_transcript(config_dir: str, session_id: str | None) -> str | None:
    """`<config_dir>/projects/**/<session_id>.jsonl` を glob で特定(munge 規則に非依存)。"""
    if not session_id:
        return None
    hits = sorted(Path(config_dir).glob(f"projects/**/{session_id}.jsonl"))
    return str(hits[0]) if hits else None


def invoke_claude(prompt: str, sandbox: str, config_dir: str,
                  env_overrides: dict | None = None, *, model: str | None = None,
                  effort: str | None = None, timeout: int = 900) -> dict:
    """`claude -p` を headless 起動し結果 JSON を返す(**唯一の API/副作用**・テストで差し替える)。"""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = config_dir
    env.update(env_overrides or {})
    cmd = _build_cmd(prompt, model, effort)
    try:
        cp = subprocess.run(cmd, cwd=sandbox, env=env, capture_output=True,
                            text=True, timeout=timeout)
        return json.loads(cp.stdout)
    except Exception as e:  # 失敗は run 結果として記録(1 件の失敗で実行全体を止めない)
        return {"_error": repr(e)}


def _cleanup_artifacts(sandbox: str, transcript: str | None) -> None:
    """run の副産物(sandbox と transcript の project dir)を削除する自動掃除。

    transcript の project dir は **名前に `sandbox` を含む時のみ**消す(treatment は live の
    `claude-home/projects/` に書くため、実セッションを誤って消さない安全ガード)。
    失敗は握りつぶす(掃除の失敗で run を壊さない)。
    """
    shutil.rmtree(sandbox, ignore_errors=True)
    if transcript:
        proj = Path(transcript).parent
        if "sandbox" in proj.name:
            shutil.rmtree(proj, ignore_errors=True)


def run_one(task_dir: str, arm_name: str, arm_cfg: dict, run_idx: int,
            sandbox_root: str, invoke=invoke_claude, *, cleanup: bool = True,
            extract=transcript_metrics.extract) -> dict:
    """1 run を実行し result 行を返す(invoke を差し替えれば API 無しでテスト可能)。

    cleanup=True(既定)なら、transcript から inline metrics を抽出して結果を自己完結化した上で
    sandbox と transcript を削除する(副産物を溜めない)。extract も差し替え可能。
    """
    task = load_task(task_dir)
    sandbox = str(Path(sandbox_root) / f"{task['id']}-{arm_name}-{run_idx}")
    prepare_sandbox(task_dir, sandbox)
    res = invoke(task["prompt"], sandbox, arm_cfg["config_dir"],
                 arm_cfg.get("env"), model=arm_cfg.get("model") or task.get("model"),
                 effort=arm_cfg.get("effort"))
    sid = res.get("session_id")
    success = run_oracle(task["meta"], sandbox, task_dir)
    transcript = find_transcript(arm_cfg["config_dir"], sid)
    inline = None
    if transcript:
        try:
            inline = extract(transcript)      # 結果を自己完結化(後で transcript を消せる)
        except Exception:
            inline = None
    if cleanup:
        _cleanup_artifacts(sandbox, transcript)
    return {"task": task["id"], "arm": arm_name, "run": run_idx, "success": success,
            "session_id": sid, "transcript": transcript, "metrics": inline,
            "cost_usd": res.get("total_cost_usd"), "error": res.get("_error"),
            "sandbox": sandbox}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="A/B bench: plain claude vs ultra-ai")
    ap.add_argument("-k", type=int, default=3, help="runs per (task,arm)")
    ap.add_argument("--tasks", nargs="*", help="task dir names under bench/tasks (default: all)")
    ap.add_argument("--arms", nargs="*", choices=list(ARMS), default=list(ARMS))
    ap.add_argument("--keep-artifacts", action="store_true",
                    help="run 後に sandbox/transcript を消さない(transcript を見たい時)")
    args = ap.parse_args(argv)

    task_dirs = [str(TASKS_DIR / t) for t in args.tasks] if args.tasks else \
        sorted(str(p) for p in TASKS_DIR.iterdir() if (p / "meta.json").exists())
    if not task_dirs:
        print(f"no tasks under {TASKS_DIR}", file=sys.stderr)
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"{stamp}.jsonl"
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for td in task_dirs:
            for arm in args.arms:
                for r in range(args.k):
                    print(f"[bench] {Path(td).name} / {arm} / run {r}", file=sys.stderr)
                    row = run_one(td, arm, ARMS[arm], r, str(SANDBOX_ROOT),
                                  cleanup=not args.keep_artifacts)
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    f.flush()
                    n += 1
    print(f"[bench] wrote {n} runs -> {out}", file=sys.stderr)
    print(out)  # stdout = results path(ab_report.py に渡す)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
