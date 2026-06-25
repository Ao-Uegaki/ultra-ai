"""detect.py — プロジェクトの種類とテスト範囲をゼロ設定で検出する(stdlib のみ, Python 3.11+)。

common.py から切り出した「プロジェクト検出」層:
  - 任意のプロジェクト設定(.ultra-ai.toml)を読み込む
  - プロジェクトの lint/型/テストのコマンドを自動検出(node / python / unknown)
  - rules/<topic>.md のドメイン(frontend/backend/ml/infra)を保守的に自動検出する
  - 変更集合から対象テストへ保守的にマップする

設計ルールは common と同じ: hook の入口に例外を投げない(安全な既定値へ縮退する)。
依存方向は detect → common の一方向(common は detect を import しない=循環なし)。
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

import common


# ---------------------------------------------------------- project config ----

def load_project_config(root: str) -> dict:
    """Read .ultra-ai.toml at the project root, if present and parseable."""
    if tomllib is None:
        return {}
    f = Path(root) / common.CONFIG_FILE
    if not f.exists():
        return {}
    try:
        with f.open("rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return {}


def verify_config(root: str) -> dict:
    """The `[verify]` section of the project config (lint/typecheck/test/scope/
    timeouts), or {} when unset. Single source for gate.py and verify.py."""
    return load_project_config(root).get(common.CONFIG_SECTION) or {}


# ------------------------------------------ stack detection (zero-config) -----

@dataclass
class Stack:
    kind: str = "unknown"           # node | python | unknown
    lint_file: str | None = None    # template using {file}
    typecheck: str | None = None
    test: str | None = None


def _has(root: str, *names: str) -> bool:
    return any((Path(root) / n).exists() for n in names)


def _which(name: str) -> bool:
    return shutil.which(name) is not None


def _has_unittest_layout(root: str) -> bool:
    """`tests/` に `test_*.py` が1つ以上あるか(stdlib unittest discover の規約)。"""
    try:
        tdir = Path(root) / "tests"
        return tdir.is_dir() and any(tdir.glob("test_*.py"))
    except Exception:
        return False


def detect_stack(root: str) -> Stack:
    if _has(root, "package.json"):
        return _detect_node(root)
    if _has(root, "pyproject.toml", "setup.cfg", "setup.py", "pytest.ini", "tox.ini"):
        return _detect_python(root)
    # manifest 無し: stdlib unittest の規約(tests/test_*.py)を python として拾う。
    # pytest 設定が無い=unittest と判断し、discover を test コマンドにする(pytest は manifest 経由で検出)。
    # これで「manifest 無し or unittest 使用」のリポは設定ゼロで gate がテストを自動実行する。
    if _has_unittest_layout(root):
        st = _detect_python(root)
        st.test = "python3 -m unittest discover -s tests"
        return st
    return Stack()


def _detect_node(root: str) -> Stack:
    pm = ("pnpm" if _has(root, "pnpm-lock.yaml")
          else "yarn" if _has(root, "yarn.lock")
          else "npm")
    try:
        scripts = json.loads((Path(root) / "package.json").read_text()).get("scripts") or {}
    except Exception:
        scripts = {}
    st = Stack(kind="node")
    if _which("eslint") or (Path(root) / "node_modules/.bin/eslint").exists():
        st.lint_file = "npx eslint --fix {file}"
    if "typecheck" in scripts:
        st.typecheck = {"pnpm": "pnpm typecheck", "yarn": "yarn typecheck",
                        "npm": "npm run typecheck"}[pm]
    elif (Path(root) / "tsconfig.json").exists():
        st.typecheck = "npx tsc --noEmit"
    if "test" in scripts:
        ts = scripts.get("test", "")
        # avoid watch-mode hangs for runners that default to watching
        if "vitest" in ts:
            st.test = "npx vitest run"
        elif "jest" in ts:
            st.test = "npx jest --watchAll=false --ci"
        else:
            st.test = {"pnpm": "pnpm test", "yarn": "yarn test", "npm": "npm test"}[pm]
    return st


def _detect_python(root: str) -> Stack:
    st = Stack(kind="python")
    if _which("ruff"):
        st.lint_file = "ruff check --fix {file}"
    elif _which("flake8"):
        st.lint_file = "flake8 {file}"
    if _which("pyright"):
        st.typecheck = "pyright"
    elif _which("mypy"):
        st.typecheck = "mypy ."
    if _which("pytest"):
        st.test = "pytest -q"
    return st


# --------------------------------------------- domain detection (rules) -------
# rules/<topic>.md のドメインを、依存/manifest から**保守的に**自動検出する(言語スコープと同型)。
# 誤検出 = 無関係 rules 注入 = cache バイトの無駄 → 専用ライブラリ/フレームワークに限定する
# (numpy/pandas/vite/Makefile のような汎用ツールは採らない)。SessionStart で速いこと:
# 深い rglob はせず、manifest 読み + 浅い glob + 数個の exists() だけで判定する。

DOMAINS = ("frontend", "backend", "ml", "infra")  # rules/<topic>.md の topic 名に一致

# node の依存名(小文字)。scoped(@angular/*)は prefix で別途判定する。
_FRONTEND_NODE = {"react", "react-dom", "vue", "svelte", "next", "nuxt",
                  "solid-js", "astro", "@remix-run/react", "preact"}
_BACKEND_NODE = {"express", "fastify", "@nestjs/core", "koa", "hapi", "@hapi/hapi"}
# python の依存名(PEP 503 正規化済み: 小文字・_/. → -)。
_BACKEND_PY = {"fastapi", "flask", "django", "starlette", "sanic", "aiohttp",
               "tornado", "falcon", "bottle"}
_ML_PY = {"torch", "tensorflow", "jax", "transformers", "scikit-learn",
          "xgboost", "lightgbm", "keras"}

_REQ_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")  # 依存指定の行頭の名前だけ


def _norm_pkg(name: str) -> str:
    """PEP 503 ざっくり正規化(python 依存名の綴り揺れを吸収): 小文字化 + `_`/`.` → `-`。"""
    return re.sub(r"[._]+", "-", name.strip().lower())


def _node_deps(root: str) -> set[str]:
    """package.json の dependencies + devDependencies のキー(小文字)。無ければ空集合。"""
    try:
        pj = json.loads((Path(root) / "package.json").read_text())
    except Exception:
        return set()
    keys: set[str] = set()
    for sect in ("dependencies", "devDependencies"):
        d = pj.get(sect)
        if isinstance(d, dict):
            keys |= {str(k).lower() for k in d}
    return keys


def _python_deps(root: str) -> set[str]:
    """pyproject([project]/[tool.poetry])と requirements*.txt(top-level)の依存名を
    PEP 503 正規化で集める。version/extras は捨て、名前だけ。深い探索はしない。"""
    names: set[str] = set()
    if tomllib is not None:
        try:
            pp = tomllib.loads((Path(root) / "pyproject.toml").read_text())
        except Exception:
            pp = {}
        proj = pp.get("project") if isinstance(pp.get("project"), dict) else {}
        for dep in (proj.get("dependencies") or []):
            m = _REQ_NAME.match(str(dep))
            if m:
                names.add(_norm_pkg(m.group(0)))
        tool = pp.get("tool") if isinstance(pp.get("tool"), dict) else {}
        poetry = tool.get("poetry") if isinstance(tool.get("poetry"), dict) else {}
        pdeps = poetry.get("dependencies")
        if isinstance(pdeps, dict):
            names |= {_norm_pkg(str(k)) for k in pdeps if str(k).lower() != "python"}
    try:
        for rq in sorted(Path(root).glob("requirements*.txt")):
            for ln in rq.read_text().splitlines():
                s = ln.strip()
                if not s or s.startswith(("#", "-")):  # コメント / -r,-e フラグは無視
                    continue
                m = _REQ_NAME.match(s)
                if m:
                    names.add(_norm_pkg(m.group(0)))
    except Exception:
        pass
    return names


def _has_shallow_ipynb(root: str) -> bool:
    """top-level と notebooks/ の浅い *.ipynb だけ見る(深い rglob はしない)。"""
    try:
        p = Path(root)
        return any(p.glob("*.ipynb")) or any((p / "notebooks").glob("*.ipynb"))
    except Exception:
        return False


def _has_infra_signals(root: str) -> bool:
    """IaC/CI の存在を浅く確認(top-level + 既知 dir のみ)。"""
    try:
        p = Path(root)
        if _has(root, "Dockerfile", "Chart.yaml"):
            return True
        for pat in ("docker-compose.y*ml", "compose.y*ml", "*.tf", "*.tfvars",
                    "kustomization.y*ml"):
            if any(p.glob(pat)):
                return True
        if any((p / ".github" / "workflows").glob("*.y*ml")):
            return True
        return (p / "k8s").is_dir()
    except Exception:
        return False


def detect_domains(root: str) -> set[str]:
    """このリポに関係する rules ドメインを決定論で検出する(stdlib・no network・保守的)。

    確信できる信号(専用フレームワーク/ライブラリ・IaC/CI ファイル)だけを採る。
    例外は決して投げない(空集合へ縮退=最悪でも従来挙動)。
    """
    out: set[str] = set()
    try:
        node = _node_deps(root)
        py = _python_deps(root)
        if (node & _FRONTEND_NODE) or any(k.startswith("@angular/") for k in node):
            out.add("frontend")
        if (node & _BACKEND_NODE) or (py & _BACKEND_PY):
            out.add("backend")
        if (py & _ML_PY) or _has_shallow_ipynb(root):
            out.add("ml")
        if _has_infra_signals(root):
            out.add("infra")
    except Exception:
        return set()
    return out


# ------------------------------------------------ impacted test scope ----------

def _porcelain_paths(changed: list[str]) -> list[str]:
    """`git status --porcelain` 行からパスを抽出(リネームは新パス側を採る)。"""
    out = []
    for ln in changed or []:
        if not ln or not ln.strip():
            continue
        # 形式: "XY PATH" (XY=2文字ステータス)。リネームは "OLD -> NEW"。
        path = ln[3:] if len(ln) > 3 and ln[2] == " " else ln.strip()
        path = path.strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip().strip('"')
        if path:
            out.append(path)
    return out


def _discover_start_dir(test: str) -> str:
    """`unittest discover` の start-dir(`-s DIR` / `--start-directory=DIR`)を取り出す。
    取れなければ unittest discover の規約どおり "tests"。"""
    try:
        toks = shlex.split(test)
        if "-s" in toks:
            i = toks.index("-s")
            if i + 1 < len(toks):
                return toks[i + 1]
        for t in toks:
            if t.startswith("--start-directory="):
                return t.split("=", 1)[1]
    except ValueError:
        pass
    return "tests"


def _is_test_path(p: str) -> bool:
    b = os.path.basename(p)
    return (b.startswith("test_") or b.endswith("_test.py")
            or p.startswith("tests/") or "/tests/" in p)


def impacted_test_cmd(test: str | None, changed: list[str], stack: "Stack",
                      root: str) -> str | None:
    """変更集合から対象テストへ保守的にマップ。確信できなければ None(=呼び出し側が full)。

    安全なケースだけを扱う(高速化は取りこぼし=未検証なのに緑、に劣後する):
      - **pytest**: 変更が *テストファイルのみ* のとき、その変更テストだけを走らせる(複数可)。
      - **stdlib unittest discover**: 変更が *単一の* テストファイル(start-dir 配下)のときだけ、
        `discover -s <dir> -p <basename>` でそのファイルだけ discover する。ドットつきモジュール形
        (`python3 -m unittest tests.test_x`)は sys.path 前提を壊し ModuleNotFoundError を招くため使わない。
    ソースファイルが1つでも変わっていれば関連テストの取りこぼしを避けて None(=full)。
    積極的な source→test マッピング(jest --findRelatedTests, vitest --changed 等)は将来作業。
    """
    if not test:
        return None
    paths = _porcelain_paths(changed)
    if not paths:
        return None

    # pytest: テストファイルのみの変更なら、それらだけを走らせる(stack でなく test 文字列で判定)。
    if "pytest" in test:
        if any(not p.endswith(".py") for p in paths):
            return None  # .py 以外も変更 → full
        if not all(_is_test_path(p) for p in paths):
            return None  # ソース変更を含む → full(取りこぼし回避)
        return "pytest -q " + " ".join(shlex.quote(p) for p in paths)

    # stdlib unittest discover: 単一テストファイル(start-dir 配下)のときだけ -p で絞る。
    if "unittest" in test and "discover" in test:
        if len(paths) != 1:
            return None  # 複数 or ソース混在 → full(取りこぼし回避)
        p = paths[0]
        if not p.endswith(".py") or not _is_test_path(p):
            return None
        start = _discover_start_dir(test)
        norm = start.rstrip("/")
        if not (p == norm or p.startswith(norm + "/")):
            return None  # start-dir 配下でない → full(0件 discover で UNKNOWN に落とさない)
        return ("python3 -m unittest discover -s " + shlex.quote(start)
                + " -p " + shlex.quote(os.path.basename(p)))

    return None
