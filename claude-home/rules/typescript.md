## TypeScript の約束ごと(一般則)

- `strict` 前提。`any` を避け `unknown`+絞り込み、外部入力は境界で検証(zod 等)してから型に乗せる。
- `null`/`undefined` を握りつぶさない。オプショナルは型で表し、早期 return で剥がす。
- 例外より型で表現できる失敗は型で(`Result`/取りうる型を1つに絞って表す型(判別共用体))。throw するなら呼び出し側の契約を明確に。
- `const` 既定、可変は局所に。純粋関数を優先し副作用は端へ。
- import は型と値を分ける(`import type`)。循環依存を避ける。
- async は await し忘れない(await し忘れて放置された非同期処理(floating promise)を残さない)。エラーは握って文脈を付ける。
- フォーマット/lint は eslint+prettier 既定。`console.log` を残さない。
