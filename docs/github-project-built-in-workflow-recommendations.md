---
title: GitHub Projects Built-in Workflow推奨設定
description: 複数プロジェクトへ横展開するための、GitHub Projects Built-in workflows の推奨有効/無効状態と運用意図を定義する
tags: [運用, GitHub Projects, Built-in Workflow, 標準化]
---

# GitHub Projects Built-in Workflow推奨設定

## 1. 目的

- プロジェクトごとの差異を減らし、Status運用を標準化する。
- レビュー進行と差し戻しを Built-in workflows で自動同期する。

## 2. 前提ステータス

本推奨は、以下の Status を前提とする。

`Backlog -> Ready -> In progress -> In review -> Approved -> Staging / Merged to Release -> Done`

## 3. 推奨状態（標準）

| Workflow 名 | 推奨 | 推奨アクション | 意図 |
| :--- | :---: | :--- | :--- |
| `Code review approved` | Enable | `Status=Approved` | 承認済みを明確化し、マージ待ちを可視化 |
| `Code changes requested` | Enable | `Status=In progress` | 差し戻し時に実作業中へ戻す |
| `Pull request linked to issue` | Enable | `Status=In review`（必要時） | PR紐づけ時にレビュー待ちへ遷移（チーム方針で選択） |
| `Pull request merged` | Enable | `Status=Staging / Merged to Release` | リリース反映前の段階を可視化 |
| `Item closed` | Enable | `Status=Done` | クローズと完了状態を一致させる |
| `Item reopened` | Enable | `Status=In progress` | 再オープン時に作業中へ戻す |

## 4. 推奨状態（補助）

| Workflow 名 | 推奨 | 補足 |
| :--- | :---: | :--- |
| `Auto-add to project` | Enable | 新規Issue/PRの取りこぼし防止 |
| `Auto-add sub-issues to project` | Enable | 親子課題運用の欠落防止 |
| `Auto-archive items` | Enable（任意） | Done滞留を抑制。アーカイブ期間はチーム合意で設定 |
| `Auto-close issue` | Disable（既定） | マージ連動で十分な場合は不要。二重運用を避ける |

## 5. 最小構成（まず入れる）

最初に有効化する最低セット:

1. `Code review approved` -> `Approved`
2. `Code changes requested` -> `In progress`
3. `Pull request merged` -> `Staging / Merged to Release`
4. `Item closed` -> `Done`

## 5.1 レビュー投入トリガー標準

- レビュー投入は `ready-for-review` ラベル単独を標準とする。
- コメントコマンド（例: `/ready`）は標準運用に含めない。
- 推奨自動処理:
  1. `ready-for-review` ラベル付与
  2. Draft解除
  3. レビュアー指定
  4. `Status=In review`

## 6. 導入時チェックリスト

- [ ] `Status` オプションに `Approved` が存在する
- [ ] `Status` オプションに `Staging / Merged to Release` が存在する
- [ ] `Code review approved` が有効
- [ ] `Code changes requested` が有効
- [ ] `Item closed` が有効
- [ ] 運用ガイドに自動遷移ルールが記載されている

## 7. 展開時の注意

- Built-in workflow は API で更新できない設定があるため、最終有効化は UI 操作が必要な場合がある。
- Status 名が異なるプロジェクトへ展開する場合、遷移先名を先に統一する。
- 親課題同期（子課題集計）は Built-in 単体では不足するため、必要に応じて Actions を併用する。
- `Closes/Fixes #xxxx` などの連動だけでは、承認時・差し戻し時・レビュー投入時の **PR/関連Issue 同時遷移** を網羅できない場合がある。
- 本リポジトリでは補完として `.github/workflows/project_status_sync.yml` を用い、PR/Issue イベントから関連Issueの Status も同期する。
- トークン設定手順は `docs/operations-guide/github-project-operations-policy.md` の「4.3 `PROJECT_AUTOMATION_TOKEN` 設定手順（参考）」を参照する。
