---
name: daily-report
description: "GitHub日報を自動生成するスキル。デフォルトでe-llabo org全リポジトリの当日の活動（コミット・PR・レビュー・Issueコメント・Issue作成）を収集し、GitHub Project #11のステータス分布・現在タスク（In Progress）・ブロック状況を中心に、リポジトリ別アコーディオンでHTML日報を生成する。--single-repoでカレントリポのみの従来動作も可能。「日報」「daily report」「今日やったこと」「作業まとめ」「作業報告」「GitHub活動レポート」「振り返り」「ステータス確認」「現在タスク」などのキーワードで使用すること。Slackへの共有用途にも対応。日付指定がなければ当日分を生成する。"
---

# GitHub 日報ジェネレーター

GitHub org全リポジトリの活動（コミット・PR・レビュー・Issueコメント・Issue作成）を収集し、**Project Statusダッシュボード**を中心にHTML日報を生成する。

## 設計方針

GitHub Project運用方針（`docs/github-project-operations-policy.md`）に準拠し、以下を重視する。

- **ステータス遷移の可視化**: Project #11 のステータス分布（Backlog → Ready → In progress → In review → Approved → Staging / Merged to Release → Done）をバーチャートで表示
- **現在タスク（In Progress）の評価**: 担当者の In Progress アイテムを一覧し、本日活動の有無で「継続作業」と「未着手」を判別
- **ブロック状況**: `Blocked=Yes` のアイテムと `Blocker Type` を明示
- **本日活動コンテキスト**: 当日のGitHub行動履歴に紐づくIssueの現在ステータスを表示

## 実行手順

### 1. 環境確認

```bash
gh auth status
```

GitHub認証を確認する。失敗した場合はユーザーに伝えて中断する。

### 2. レポート生成スクリプトの実行

`~/.claude/bin/daily-report.py` を実行して日報を生成する。

**デフォルト（org全リポスキャン + Project Status）:**
```bash
python3 ~/.claude/bin/daily-report.py [--date YYYY-MM-DD]
```

**単一リポモード（従来動作）:**
```bash
python3 ~/.claude/bin/daily-report.py --single-repo [--date YYYY-MM-DD]
```

**Project Statusなし:**
```bash
python3 ~/.claude/bin/daily-report.py --no-project [--date YYYY-MM-DD]
```

### オプション一覧

| オプション | 説明 | デフォルト |
|---|---|---|
| `--date YYYY-MM-DD` | 対象日 | 当日 |
| `--single-repo` | カレントリポのみ対象（従来動作） | off（org全リポ） |
| `--org ORG` | org名を明示指定 | カレントリポのowner |
| `--project NUMBER` | GitHub Project番号 | `GITHUB_PROJECT_NUMBER` 環境変数 or `11` |
| `--no-project` | Project Statusセクションを無効化 | off |
| `--output-dir DIR` | 出力先ディレクトリ | `~/reports` |
| `--no-open` | ブラウザを開かない | off |
| `--backlog-space` | Backlogスペース | 環境変数 `BACKLOG_SPACE` |
| `--backlog-api-key` | Backlog APIキー | 環境変数 `BACKLOG_API_KEY` |

- 出力先: `~/reports/daily-report-YYYY-MM-DD.html`
- 生成後に自動でブラウザを開く

### 3. 結果の確認

生成されたHTMLファイルのパスをユーザーに伝える。ブラウザで開けない環境の場合はファイルパスを表示する。

## 日報の構成

### 1. 📊 Project Status ダッシュボード（メインセクション）

Project #11 のステータスに基づく進捗ダッシュボード。

- **ステータス分布バー**: 全アイテムのステータス比率を色分けバーで表示
  - `In progress`(緑), `In review`(青), `Approved`(紫), `Staging / Merged to Release`(黄), `Ready`(灰), `Backlog`(薄灰), `Done`(濃灰)
- **🗓 ステータス別タスク**: 担当・本日活動アイテムをカテゴリ別に統合表示

| カテゴリ | 判定条件 | バッジ色 |
|---|---|---|
| **ブロック中** | 全ステータス + Blocked=Yes | 赤 |
| **継続作業** | In Progress + 本日活動あり | 緑 |
| **未着手** | In Progress + 本日活動なし | 黄 |
| **レビュー待ち** | In Review | 青 |
| **本日活動** | 上記以外のステータス + 本日活動あり | 黄(薄) |

### 2. リポジトリ別セクション（`<details>` アコーディオン）

各リポについて以下を表示（活動がある場合のみ）:

1. **🔀 PR & コミット** — PR単位でコミットをグルーピング、関連Issue付き
2. **📝 レビュー** — インラインコメント、PR会話コメント、レビューアクション、マージしたPR
3. **📌 Issueコメント** — 純粋なIssueへのコメント（PR除く）
4. **🎫 作成したIssue** — 当日作成したIssue

### 3. 📎 Backlog（設定時のみ）

Backlog活動（設定時のみ）。

### 4. 🗓 明日の予定（フォールバック）

Project データがない場合のみ表示。従来の「明日の予定」（オープンPR・レビュー依頼・アサイン済みIssue）にフォールバックする。

## Slack連携

HTML内に「Slackにコピー」ボタンを設置。ボタンクリックで日報本文がリッチテキストとしてクリップボードにコピーされ、Slackに貼り付けるとリンクが保持される。

Slack出力にもProject Status（ステータス別タスク一覧）とリポ別活動がグルーピングされる。

## 注意事項

- `gh` CLIがインストール・認証済みであることが前提
- Project Status 取得には `project` 権限を含むトークンが必要（権限不足時は自動スキップ）
- GitHub Search APIのレート制限（30回/分）に注意（通常の日報生成では問題にならない）
- プライベートリポジトリでも認証があれば動作する
- org全リポスキャンはGitリポジトリ外からでも `--org` 指定で実行可能
