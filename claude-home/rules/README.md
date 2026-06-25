# rules/ — on-demand の約束ごと(ECC の always-loaded rules/ とは別物)

ここの `<topic>.md` は **SessionStart に、このリポに関係するものだけが、毎回まったく同じ文章で(数字・時刻を混ぜない)文脈へ読み込ませ**られる
(`resume_context._rules_block`)。同じ文章ならプロンプトキャッシュが効いて速く・安い。常時 全部ロードはしない=キャッシュを無駄にしない・関係ないトピックは入らない。

- **言語**(自動スコープ): `python.md` / `typescript.md` は `detect_stack`(Python/Node 判定)で選ばれる。
- **ドメイン**(自動スコープ): `frontend.md` / `backend.md` / `ml.md` / `infra.md` は、リポの依存/manifest から
  `detect_domains` が**自動検出**して入る(言語と同型。手動で一度設定しないと起きず眠ってしまう、を解消)。検出信号は
  保守的(誤検出=無関係 rules でバイトの無駄): frontend=react/vue/@angular 等、backend=express/fastapi/django 等、
  ml=torch/tensorflow 等 or `*.ipynb`、infra=Dockerfile/`*.tf`/k8s/`.github/workflows` 等(numpy/pandas/vite/Makefile
  のような汎用は採らない)。
- **手動の上乗せ/取りやめ**: `.ultra-ai.toml` の `[ua-rules] domains = [...]` は今も**加算**(自動検出に上乗せ)。
  自動検出を止めたいときは `[ua-rules] auto = false`(手動 `domains` は残る)。
- 無効化: `UA_RULES=0`(rules 全体)。
- `/ua-rules` skill は任意(閲覧・追記・その場の引き)。

**書き方**: 各ファイルは **短く・要点のみ**(網羅しない=ためこまない)。プロジェクト固有の話は書かない
(それは学習レイヤが訂正から獲得する)。ここに書くのは「言語/領域の一般則」だけ。
