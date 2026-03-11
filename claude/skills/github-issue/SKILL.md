---
name: github-issue
description: "GitHub IssueとProject操作を自動化するスキル。e-llabo Organization Project #11（AubeQuest (portal)）のIssue起票、ステータス遷移（ゲートチェック付き）、ブロック管理、親子課題同期、週次メンテナンスチェックに対応する。「issue作成」「issue起票」「チケット作成」「チケット起票」「ステータス変更」「ステータス更新」「ブロック設定」「ブロック解除」「issue一覧」「project管理」「親子課題同期」「週次チェック」「GitHub Issue」「プロジェクト管理」「issue list」「チケット一覧」などのキーワードで使用すること。"
---

# GitHub Issue & Project 操作ツール

GitHub Issue の起票、ステータス遷移（ゲートチェック付き）、ブロック管理、親子課題同期、週次メンテナンスチェックを自動化する。

## 実行手順

### 1. 環境確認

```bash
gh auth status
```

GitHub認証を確認する。失敗した場合はユーザーに伝えて中断する。

### 2. サブコマンドの実行

`~/.claude/bin/github-issue.py` を実行してIssue/Project操作を行う。

#### 2.1 Issue起票 (create)

**起票先リポジトリの確認（必須）:**

起票前に、ユーザーが `--repo` を明示指定していない場合は、起票先リポジトリをユーザーに確認する。
特に以下のキーワードが含まれる場合は、デフォルト（ELL_portal）以外の候補を提示すること:

| キーワード | 候補リポジトリ |
|---|---|
| 麻雀、mahjong | `e-llabo/AUBE_mahjong_project` |

確認方法: ユーザーに「起票先は `e-llabo/ELL_portal` で良いですか？」と聞くか、キーワードから候補を提示する。

**テンプレート選択（`--template`）:**

ユーザーの依頼内容に応じて適切なテンプレートを選択する。

| テンプレート | 用途 | 自動付与 |
|---|---|---|
| `task`（デフォルト） | 実装・調査・運用タスク全般 | — |
| `bug` | 不具合の一次起票（トリアージ前提） | `bug` ラベル、`[BUG]` プレフィクス |
| `feature-parent` | 1機能を横断管理する親Issue | `[FEATURE]` プレフィクス |
| `feature-child-req` | 要件調整の子Issue | — |
| `feature-child-spec` | 仕様書作成の子Issue | — |
| `feature-child-impl` | 実装（テスト込み）の子Issue | — |

テンプレート使い分け判断:
- 「バグ」「不具合」「エラー」「障害」→ `bug`
- 「機能開発」で要件→仕様→実装を分けて管理 → `feature-parent` + `feature-child-*`
- 小中規模や同時進行できる作業 → `task` 1件で十分
- 親Issueは進捗管理目的。実作業・受け入れ条件は子Issue側で具体化する

Bug (Light) の例外ルール:
- Backlog 時は Milestone / Target date の設定を必須化しない
- Ready 化時に Milestone / Target date / Priority を必須設定する

```bash
python3 ~/.claude/bin/github-issue.py [--repo OWNER/NAME] create \
  --title "タイトル" \
  [--template task|bug|feature-parent|feature-child-req|feature-child-spec|feature-child-impl] \
  [--body "本文"] \
  [--milestone "マイルストーン名"] \
  [--target-date YYYY-MM-DD] \
  [--estimate "0.5d"] \
  [--status Backlog|Ready] \
  [--labels "label1,label2"] \
  [--assignee "username"] \
  [--parent 123] \
  [--depends-on "45,67"]
```

- グローバルオプション（`--repo`, `--org`, `--project`）はサブコマンド名の**前**に置くこと。
- `--body` 省略時は `--template` に応じた本文を自動構築する。
- ユーザーが概要/完了条件/スコープを口頭で伝えた場合、Claude がそれを構造化して `--body` に渡す。
- `--status Ready` 指定時は Ready化ゲート（Milestone/Target date必須）を自動チェックする。
- Project紐づけが不要な場合は、スクリプト実行後に手動でProject解除する。

#### 2.2 ステータス変更 (status)

```bash
python3 ~/.claude/bin/github-issue.py status ISSUE_NUMBER NEW_STATUS [--force] [--with-pr]
```

- ゲートチェック付き遷移を実行する。
- `--force` でゲートチェックをスキップできる。
- `--with-pr` で `In progress` 移行時にDraft PRを自動作成する。
- ステータス値: `Backlog`, `Ready`, `"In progress"`, `"In review"`, `Approved`, `"Staging / Merged to Release"`, `Done`

#### 2.3 Issue一覧 (list)

```bash
python3 ~/.claude/bin/github-issue.py list \
  [--status "In progress"] \
  [--blocked] \
  [--assignee "username"] \
  [--parent 123] \
  [--all]
```

- フィルタ条件に合うIssueをProject情報付きで表示する。
- デフォルトはOPENのみ。`--all` でCLOSEDも含む。

#### 2.4 ブロック設定 (block)

```bash
python3 ~/.claude/bin/github-issue.py block ISSUE_NUMBER \
  --type "Waiting External" \
  [--reason "理由テキスト"]
```

- `Blocked=Yes`、`Blocker Type`、`Blocked Since=今日` を設定する。
- `--reason` 指定時はIssueコメントも追加する。
- `--type` の選択肢: `"Waiting External"`, `"Waiting Other Task"`, `"Need Decision"`, `"Env/Infra"`, `"Other"`

#### 2.5 ブロック解除 (unblock)

```bash
python3 ~/.claude/bin/github-issue.py unblock ISSUE_NUMBER [--comment "解除理由"]
```

- `Blocked=No` に設定し、`Blocker Type` と `Blocked Since` をクリアする。

#### 2.6 親子課題同期 (sync-parents)

```bash
python3 ~/.claude/bin/github-issue.py sync-parents [--dry-run]
```

- 子課題のステータス集合から親課題のステータスを自動計算・更新する。
- 全子課題がCLOSEDの場合、親課題もcloseする。
- `--dry-run` で変更を反映せず差分のみ確認できる。

#### 2.7 週次メンテナンスチェック (weekly-check)

```bash
python3 ~/.claude/bin/github-issue.py weekly-check
```

- Done/Closed 不整合、3日超 Blocked、ステータス矛盾、親子課題同期候補を検出してレポートする。

### 共通オプション

| オプション | 説明 | デフォルト |
|---|---|---|
| `--org ORG` | GitHub org名 | `e-llabo` |
| `--project NUMBER` | Project番号 | `11` |
| `--repo OWNER/NAME` | リポジトリ | `e-llabo/ELL_portal` |

### 3. 結果の確認

コマンド実行結果をユーザーに伝える。エラー時は原因と対処法を示す。

## ゲートチェック

ステータス遷移時に運用方針に基づくバリデーションを自動実行する。

| 遷移先 | チェック内容 |
|---|---|
| Ready | Milestone設定済、Target date設定済、完了条件記載済、依存関係明示済 |
| In progress | 関連PR（Draft可）存在、PR本文に実装方針・影響範囲記載、Issueコメントで作業分解宣言済、Blocked整合性 |
| In review | 非Draft PR存在、Target dateが過去でない、依存Issueの状態確認 |

`--force` フラグでスキップ可能。

## 起票ルール（Claude向けガイダンス）

Issue起票を求められた際は、以下の基準をユーザーに確認・適用すること:

- **Issue必須**: 実装PR、設計docs PR、`> 0.5d` の調査、複数人連携
- **Issue任意（PR直行可）**: `<= 0.5d` かつ単独作業 → PR本文に `No Issue (tiny task)` 明記、`tiny-task` ラベル推奨
- **推奨**: 設計書（docs PR）なしの作業着手は原則非推奨。例外時は `design-debt` ラベル付与 + 後続docs PR計画

## 注意事項

- `gh` CLIがインストール・認証済みであることが前提
- Project操作には `project` 権限を含むトークンが必要
- ステータス変更は原則ゲートチェックを通す（`--force` は例外用）
- 親課題のステータスは直接変更せず `sync-parents` を使用する
