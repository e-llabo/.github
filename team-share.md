GitHub運用ルール整備のお知らせ

組織全体のGitHub運用を統一します。以下の方針で進めますので、フィードバックください。

---

【背景】
- Issue の書き方がバラバラ（タイトルだけ、英語/日本語混在など）
- ラベルが活用されていない
- 後から見たときに背景やゴールがわからないIssueが多い

【やること】

1. .github リポに組織共通テンプレート・ラベル・運用ルールを整備
2. Claude Code のスキルでIssue作成を自動化（/create-issue）
3. 日報生成スキル（/daily-report）の整備

【Issue作成の新しいフロー】

今まで: 人間がGitHubで手動作成 → 品質バラバラ
これから: Claude Code に「このバグのissue切って」と言う → スキルが対話で背景・完了条件を引き出して起票

ユーザー → /create-issue → 種別判定 → 対話で情報収集 → Issue自動起票 → ラベル付与 → ProjectV2に追加

Web UIからの作成もテンプレートで最低限ガードしますが、基本はClaude Code経由を推奨します。

【Issueの種別】

bug / feature / task / epic の4種類。タイトルは [BUG] [FEAT] [TASK] プレフィックス + 日本語。

【ラベル】

種別: epic / feature / bug / task / docs / planning / marketing / design
フェーズ: requirements / implementation
運用: blocked / ready-for-review / design-debt / tiny-task

【親子Issue】

- Epic（大目標）→ 親リポに作成
- Story/Feature → 各リポにIssue、Epicの Sub-issue として紐づけ
- Task → Issue本文のチェックリスト or さらに Sub-issue

【ブランチ命名】

{type}/issue-{番号}-{短い説明}
例: feat/issue-143-name-overflow

【ProjectV2】

Backlog → Ready → In progress → In review → Done

【テスト運用のお願い】

まず数名にテスト運用をお願いして、1-2週間フィードバックをもらってから全社展開します。普段の作業で /create-issue と /daily-report を使ってみて、使いにくい点や過不足があれば教えてください。
