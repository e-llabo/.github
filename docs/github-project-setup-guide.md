---
title: GitHub Project セットアップガイド
description: 運用方針に準拠した GitHub Project の作成手順・フィールド定義・前提条件
tags: [運用, GitHub Projects, セットアップ]
---

# GitHub Project セットアップガイド

運用方針（`github-project-operations-policy.md`）に準拠した GitHub Project を `gh` CLI で構築する手順。

## 前提条件

- `gh` CLI がインストール・認証済みであること
- Project を作成する org / ユーザーアカウントの管理権限を持っていること
- トークンに `project` スコープが含まれていること（`gh auth status` で確認）

## 1. Project 作成

```bash
# org の場合
gh project create --owner <ORG_NAME> --title "<PROJECT_TITLE>" --format json

# 個人アカウントの場合
gh project create --owner @me --title "<PROJECT_TITLE>" --format json
```

出力から `number` を控える（以降 `<PROJECT_NUMBER>` として使用）。

## 2. カスタムフィールド作成

### 2.1 フィールド一覧

| フィールド | タイプ | 選択肢 | 用途 |
|---|---|---|---|
| Status | SINGLE_SELECT | ※デフォルト生成 | 進捗管理 |
| Blocked | SINGLE_SELECT | Yes, No | 保留状態 |
| Blocker Type | SINGLE_SELECT | Waiting External, Waiting Other Task, Need Decision, Env/Infra, Other | 保留理由 |
| Blocked Since | DATE | — | 保留開始日 |
| Priority | SINGLE_SELECT | High, Medium, Low | 優先度 |
| Estimate | TEXT | — | 工数見積り（例: 0.5d, 2d） |
| Target date | DATE | — | 期日 |

> **Note**: `Status` フィールドは Project 作成時に自動生成される。選択肢のカスタマイズは別途 GraphQL API で行う（後述）。

### 2.2 フィールド作成コマンド

```bash
OWNER="<ORG_OR_USER>"  # org名 または @me
PN=<PROJECT_NUMBER>

# Blocked
gh project field-create "$PN" --owner "$OWNER" --name "Blocked" \
  --data-type "SINGLE_SELECT" --single-select-options "Yes,No"

# Blocker Type
gh project field-create "$PN" --owner "$OWNER" --name "Blocker Type" \
  --data-type "SINGLE_SELECT" \
  --single-select-options "Waiting External,Waiting Other Task,Need Decision,Env/Infra,Other"

# Blocked Since
gh project field-create "$PN" --owner "$OWNER" --name "Blocked Since" \
  --data-type "DATE"

# Priority
gh project field-create "$PN" --owner "$OWNER" --name "Priority" \
  --data-type "SINGLE_SELECT" --single-select-options "High,Medium,Low"

# Estimate
gh project field-create "$PN" --owner "$OWNER" --name "Estimate" \
  --data-type "TEXT"

# Target date
gh project field-create "$PN" --owner "$OWNER" --name "Target date" \
  --data-type "DATE"
```

## 3. Status フィールドのカスタマイズ

デフォルトの Status 選択肢を運用方針に合わせて更新する。

### 3.1 運用方針のステータスフロー

```
Backlog → Ready → In progress → In review → Approved → Staging / Merged to Release → Done
```

### 3.2 Status オプション更新手順

Status フィールドの選択肢変更は GraphQL API で行う。

```bash
# Step 1: Project ID と Status フィールド ID を取得
PROJECT_ID=$(gh project view "$PN" --owner "$OWNER" --format json -q .id)

FIELD_JSON=$(gh project field-list "$PN" --owner "$OWNER" --format json)
STATUS_FIELD_ID=$(echo "$FIELD_JSON" | jq -r '.fields[] | select(.name == "Status") | .id')

echo "Project ID: $PROJECT_ID"
echo "Status Field ID: $STATUS_FIELD_ID"

# Step 2: 現在の選択肢を確認
echo "$FIELD_JSON" | jq '.fields[] | select(.name == "Status") | .options'

# Step 3: 不足しているオプションを追加
# ※ デフォルトでは Todo / In Progress / Done の3つが存在する。
#    以下は運用方針に合わせた追加・名称変更の例。

# 新規オプション追加（Backlog, Ready, In review, Approved, Staging / Merged to Release）
for OPT_NAME in "Backlog" "Ready" "In review" "Approved" "Staging / Merged to Release"; do
  gh api graphql -f query='
    mutation($projectId: ID!, $fieldId: ID!, $name: String!) {
      updateProjectV2Field(input: {
        projectId: $projectId
        fieldId: $fieldId
        singleSelectField: {
          options: { name: $name }
        }
      }) {
        field { ... on ProjectV2SingleSelectField { id } }
      }
    }' -f projectId="$PROJECT_ID" -f fieldId="$STATUS_FIELD_ID" -f name="$OPT_NAME"
  echo "Added: $OPT_NAME"
done
```

> **Note**: デフォルトの `Todo` は `Backlog` + `Ready` に分割して運用する。不要なデフォルトオプションは GitHub Web UI から削除するか、GraphQL の `deleteProjectV2FieldOption` mutation で削除する。

### 3.3 デフォルトオプションの整理

Project 作成直後のデフォルト選択肢と、運用方針との対応:

| デフォルト | 対応 |
|---|---|
| Todo | 削除（`Backlog` + `Ready` に置換） |
| In Progress | `In progress` にリネーム（大文字小文字の統一） |
| Done | そのまま利用 |

リネーム・削除は GitHub Web UI（Project Settings > Status フィールド）で行うのが確実。

## 4. Built-in Workflow の有効化

Project Settings > Workflows から以下を有効化する:

| Workflow | 動作 |
|---|---|
| Item added to project | → `Status: Backlog` |
| Item closed | → `Status: Done` |
| Item reopened | → `Status: In progress` |
| Code review approved | → `Status: Approved` |
| Code changes requested | → `Status: In progress` |

> 詳細は `github-project-built-in-workflow-recommendations.md` を参照。

## 5. リポジトリとの連携設定

### 5.1 Project をリポジトリにリンク

GitHub Web UI で Project Settings > Manage access からリポジトリを追加する。
または:

```bash
gh project link "$PN" --owner "$OWNER" --repo "<OWNER>/<REPO>"
```

### 5.2 Actions 用の Secrets / Variables 設定

各リポジトリの Settings > Secrets and variables > Actions に以下を設定する:

| 種別 | 名前 | 値 |
|---|---|---|
| Secret | `PROJECT_AUTOMATION_TOKEN` | `repo` + `read:org` + `project` スコープを持つ PAT |
| Variable | `PROJECT_OWNER` | org名 またはユーザー名（省略時はワークフローのデフォルト値を使用） |
| Variable | `PROJECT_NUMBER` | Project 番号（省略時はワークフローのデフォルト値を使用） |

### 5.3 トークン要件

| トークン種別 | 必要スコープ |
|---|---|
| Classic PAT | `repo`, `read:org`, `project` |
| Fine-grained PAT | 対象リポジトリ権限 + Organization `Projects` の read/write |

org が SSO 必須の場合は、トークン作成後に `Configure SSO` で対象 org を承認すること。

## 6. 動作確認

```bash
# トークン確認
gh api user

# Project アクセス確認
gh project view "$PN" --owner "$OWNER" --format json -q .id

# フィールド一覧確認
gh project field-list "$PN" --owner "$OWNER" --format json | jq '.fields[] | {name, type}'
```

## 7. ゲートチェック一覧（参考）

ワークフローが自動検証するステータス遷移ゲート:

| 遷移先 | チェック内容 |
|---|---|
| Ready | Milestone 設定済、Target date 設定済、完了条件記載済、依存関係明示済 |
| In progress | 関連 PR（Draft 可）存在、PR 本文に実装方針・影響範囲記載、Blocked 整合性 |
| In review | 非 Draft PR 存在、Target date が過去でない、依存 Issue の状態確認 |

詳細は `github-project-operations-policy.md` を参照。
