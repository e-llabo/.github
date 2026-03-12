---
name: github-issue
description: "GitHub IssueとProject操作を自動化するスキル。e-llabo Organization内の全プロジェクト（portal, mahjong, infra等）に対応。Issue起票時は対話形式でプロジェクト・種別・内容を確認し、要件が揃い次第実行する。「issue作成」「issue起票」「チケット作成」「チケット起票」「ステータス変更」「ステータス更新」「ブロック設定」「ブロック解除」「issue一覧」「project管理」「親子課題同期」「週次チェック」「GitHub Issue」「プロジェクト管理」「issue list」「チケット一覧」などのキーワードで使用すること。"
---

# GitHub Issue & Project 操作ツール

GitHub Issue の起票・ステータス遷移・ブロック管理・親子課題同期を **対話形式** で行う。
特定プロジェクト固定ではなく、e-llabo org内の全プロジェクトに柔軟に対応する。

## 前提

- スクリプト: `~/.claude/bin/github-issue.py`
- Windows環境では `python`、それ以外は `python3` で実行する
- `gh` CLI がインストール・認証済みであること

## Issue起票フロー（対話型）

ユーザーが Issue 作成を依頼した場合、以下の順で対話的に要件を確認する。
**ユーザーが最初のメッセージで十分な情報を与えている場合は、既に分かっている項目をスキップして確認に進む。**

### Step 1: 環境確認（自動・無言で実行）

```bash
gh auth status
```

失敗した場合のみユーザーに伝えて中断する。

### Step 2: プロジェクト特定

ユーザーの発言からどのプロジェクトか推定する。推定できない場合は聞く。

**キーワード→プロジェクト推定ルール:**

| キーワード | リポジトリ | Project番号 |
|---|---|---|
| 麻雀、mahjong、牌、対局 | `e-llabo/AUBE_mahjong_project` | `16` |
| ポータル、portal、管理画面 | `e-llabo/ELL_portal` | `11` |
| インフラ、infra、デプロイ、CI | `e-llabo/ELL_portal` | `12` |

推定できない場合のClaudeの問いかけ例:
> 「どのプロジェクトのIssueですか？」
> 1. AUBE_Mahjong_project（麻雀）
> 2. ELL_portal（ポータル）
> 3. その他（リポジトリ名を教えてください）

**判断に迷ったら `list-projects` で一覧を取得して提示する:**

```bash
python ~/.claude/bin/github-issue.py list-projects
```

### Step 3: Issue種別の判定

ユーザーの発言から種別を推定する。曖昧な場合は聞く。

**推定ルール:**
- 「バグ」「不具合」「エラー」「動かない」「壊れた」→ `bug`
- 「機能」「新しく作る」「追加」で規模が大きい → `feature-parent` + 子Issue
- それ以外の作業 → `task`

**Claudeの問いかけ例（推定できない場合）:**
> 「これはどのような種別ですか？」
> 1. バグ修正
> 2. 新機能（大規模 → 親子Issue構成）
> 3. タスク（実装・調査・改善など）

### Step 4: 詳細のヒアリング

種別に応じて必要な情報を収集する。**既にユーザーが伝えている項目はスキップする。**

**共通（必須）:**
- タイトル（なければ内容から提案）
- 概要・背景

**共通（任意・聞かなくてもよい）:**
- 担当者（`--assignee`）
- マイルストーン（`--milestone`）
- 期日（`--target-date`）
- 見積り（`--estimate`）
- 依存Issue（`--depends-on`）

**bug の場合に追加で聞くこと:**
- 再現手順
- 期待する動作 vs 実際の動作

**feature-parent の場合:**
- 子Issueの分割方針を提案する（要件/仕様/実装）
- ユーザーが同意したら親Issueと子Issueを一括起票する

### Step 5: 確認→実行

収集した情報を整理して確認する。

**Claudeの確認表示例:**
```
以下の内容でIssueを起票します。よろしいですか？

  プロジェクト: AUBE_Mahjong_project (#16)
  リポジトリ:   e-llabo/AUBE_mahjong_project
  種別:         bug
  タイトル:     [BUG] 対局画面でスコアが表示されない
  ラベル:       bug
  ステータス:   Backlog
```

ユーザーが承認したら実行する:

```bash
python ~/.claude/bin/github-issue.py \
  --repo e-llabo/AUBE_mahjong_project \
  --project 16 \
  create \
  --title "対局画面でスコアが表示されない" \
  --template bug \
  --body "..." \
  --status Backlog
```

**重要: グローバルオプション（`--repo`, `--org`, `--project`）はサブコマンド名の前に置く。**

### Step 6: 結果報告

作成されたIssueのURL・番号を伝える。エラー時は原因と対処法を示す。

## テンプレートと自動付与

| テンプレート | 用途 | 自動ラベル | タイトルプレフィクス |
|---|---|---|---|
| `task` | 実装・調査・運用タスク全般 | `task` | `[TASK]` |
| `bug` | 不具合の一次起票 | `bug` | `[BUG]` |
| `feature-parent` | 機能の親Issue | `feature` | `[FEATURE]` |
| `feature-child-req` | 要件調整の子Issue | `feature`, `requirements` | `[FEATURE]` |
| `feature-child-spec` | 仕様書作成の子Issue | `feature`, `requirements` | `[FEATURE]` |
| `feature-child-impl` | 実装の子Issue | `feature`, `implementation` | `[FEATURE]` |

## その他のコマンド（非対話型）

以下のコマンドはユーザーの指示に従って直接実行する。`--project` はユーザーの文脈から適切に指定する。

### ステータス変更

```bash
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N status ISSUE_NUMBER NEW_STATUS [--force] [--with-pr]
```

ステータス値: `Backlog`, `Ready`, `"In progress"`, `"In review"`, `Approved`, `"Staging / Merged to Release"`, `Done`

### Issue一覧

```bash
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N list [--status "In progress"] [--blocked] [--assignee "username"] [--parent 123] [--all]
```

### 親子課題ツリー表示

```bash
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N tree [--root 123] [--all]
```

### ブロック設定 / 解除

```bash
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N block ISSUE_NUMBER --type "Waiting External" [--reason "理由"]
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N unblock ISSUE_NUMBER [--comment "解除理由"]
```

ブロック種別: `"Waiting External"`, `"Waiting Other Task"`, `"Need Decision"`, `"Env/Infra"`, `"Other"`

### 親子課題同期

```bash
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N sync-parents [--dry-run]
```

### 週次メンテナンスチェック

```bash
python ~/.claude/bin/github-issue.py --repo OWNER/NAME --project N weekly-check
```

### Project一覧

```bash
python ~/.claude/bin/github-issue.py list-projects
```

## ゲートチェック

ステータス遷移時に運用方針に基づくバリデーションを自動実行する。

| 遷移先 | チェック内容 |
|---|---|
| Ready | Milestone設定済、Target date設定済、完了条件記載済、依存関係明示済 |
| In progress | 関連PR存在、PR本文に実装方針・影響範囲記載、作業分解宣言済、Blocked整合性 |
| In review | 非Draft PR存在、Target dateが過去でない、依存Issueの状態確認 |

`--force` フラグでスキップ可能。

## 起票ルール

- **Issue必須**: 実装PR、設計docs PR、`> 0.5d` の調査、複数人連携
- **Issue任意（PR直行可）**: `<= 0.5d` かつ単独作業 → PR本文に `No Issue (tiny task)` 明記、`tiny-task` ラベル推奨
