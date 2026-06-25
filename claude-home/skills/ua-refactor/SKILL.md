---
name: ua-refactor
description: ultra-ai が生成したコード(直近の diff)に挙動不変のリファクタパスをかける。PASS の関門を安全網に、型で分類し、小さく分けて適用し、refactor/fix を別コミットにする。既にきれいなら止める。
---

ultra-ai が生成したコードに、挙動不変のリファクタパスをかける。リファクタの唯一の前提は
「挙動不変を保証できること」。ultra-ai の PASS の関門(PASS になるまで直す繰り返し)がその安全網になる。

手順:
1. **PASS ベースライン(ハード前提)** — まず検証が PASS か確認。PASS/検証可能でなければ refactor しない
   (挙動不変を保証できない)。対象にテストが無ければ、先に characterization test を足すか、
   その旨を報告して止める。
2. **対象の確定** — 直近の生成物 = 最後の checkpoint 以降の diff / touched files。スコープを宣言。
3. **隔離調査(本筋の文脈を生コードで埋めない切り分け=調べ物はサブエージェントに任せ、結論だけ受け取る)** — 広い読み込みは reviewer(sonnet)/ built-in Explore の隔離
   コンテキストへ委譲。返すのは型で分類した要点だけの指摘(file:line + 一行の修正案):
   重複集約(DRY)/ 構造硬化(soft→hard)/ 一貫性 / デッドコード / 複雑さ分解 / 命名・docs整合。
   各指摘に「refactor(挙動不変)か fix(挙動変化)か」と payoff(高/中/低)を付ける。
4. **トリアージ** — rule-of-three・YAGNI・payoff で間引く。低 payoff の churn は捨てる。refactor と
   fix を分離する。**既にきれいなら「きれい」と報告して止める**(churn を作らない)。
5. **小さく適用 → 即検証** — 1 ステップ = 1 種類。各ステップ後に Stop の関門が PASS を確認する
   (ゼロトークン)。FAIL になったらそのステップを revert する。
6. **型別にチェックポイント** — PASS になったら `/ua-checkpoint`。`refactor(...)` と `fix(...)` を
   同じコミットに混ぜない。feature とも分ける。

ルール:
- 挙動不変が一次。PASS を保てない変更は refactor ではない → 止める。
- きれいなコードを「簡潔にするため」だけに触らない(payoff 必須)。
- 機構は勘で足さない。既存(reviewer / gate / ua-checkpoint)を使う。
