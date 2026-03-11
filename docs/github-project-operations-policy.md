---
title: GitHub Project運用方針（Status/親子課題/自動化）
description: GitHub Project #11 のステータス運用、親子課題同期、クローズ連動、移行後のメンテナンス手順を定義する運用方針
tags: [運用, GitHub Projects, ステータス管理, 親子課題, 自動化]
---

# GitHub Project運用方針（Status/親子課題/自動化）

## 1. 目的

- Project のステータスを「実作業の状態」と一致させる。
- レビュー通過後の滞留、リリース反映待ち、親子課題の進捗乖離を可視化する。
- Backlog 移行直後のノイズ（未着手チケットの誤進行）を抑制する。

## 1.1 成果物リポジトリ方針

- 原則として docs はモノレポ（`e-llabo/ELL_portal`）で管理する。
- ただし以下は専用リポジトリで成果管理する運用を許容する。
  - 複雑な画面デザイン成果物
  - プレゼンテーション資料
  - 複数領域にまたがるインフラ作業
- 専用リポジトリで管理する場合も、親Issueまたは関連Issueに成果物リポジトリ/PRリンクを記録する。

## 2. 適用対象

- GitHub Project: `e-llabo` Organization Project `#11`（AubeQuest（portal））
- 対象リポジトリ: `e-llabo/ELL_portal`

## 3. Status 定義

推奨フロー:

`Backlog -> Ready -> In progress -> In review -> Approved -> Staging / Merged to Release -> Done`

定義:

- `Backlog`: 未着手の棚卸し状態（移行直後・再優先度付け前）
- `Ready`: 着手可能（要件/前提が揃っている）
- `In progress`: 実装・調査・修正の実作業中
- `In review`: PRがあり、レビューコメント/レビューイベントが発生
- `Approved`: 関連PRが承認済み（APPROVED）
- `Staging / Merged to Release`: リリースブランチ反映済みで本番未反映
- `Done`: 課題完了（Issue closed、または運用上クローズ済み扱い）

## 4. 自動化ルール

- Built-in workflow `Code review approved` を有効化し、`Status=Approved` へ遷移する。
- Built-in workflow `Code changes requested` を有効化し、`Status=In progress` へ戻す。
- `Closed` は `Done` へ寄せる（少なくとも日次/週次で整合）。
- 親課題は子課題ステータスから同期する（手動で親を直接進めない）。
- 親課題同期はイベント都度ではなく、低負荷時間の定期実行を基本とする。
- `Closes/Fixes #xxxx` のみでは PR と関連Issueの同時同期が揃わないケースがあるため、補完Workflowで関連Issueの Status を同期する。

### 4.1 親課題同期ワークフロー設定

- Workflow: `.github/workflows/project_parent_sync.yml`
- 実行方式:
  - `schedule`: `13 20 * * *`（UTC）= 毎日 `05:13`（JST）
  - `workflow_dispatch`: 手動実行（緊急補正用）
- 必須 Secret:
  - `PROJECT_AUTOMATION_TOKEN`（`project` 権限を含むトークン）

### 4.2 PR/Issue補完同期ワークフロー設定

- Workflow: `.github/workflows/project_status_sync.yml`
- 目的: Built-in の取りこぼしを補完し、PRイベント（承認/差し戻し/レビュー投入/マージ）と関連Issueの Status を同期する
- 主要イベント:
  - `pull_request_review(submitted)` -> `Approved` / `In progress`
  - `pull_request(opened|ready_for_review|synchronize|closed)` -> `In review` / `Staging / Merged to Release`
  - `issues(closed|reopened)` -> `Done` / `In progress`

### 4.3 `PROJECT_AUTOMATION_TOKEN` 設定手順（参考）

1. リポジトリ `Settings` -> `Secrets and variables` -> `Actions` で `PROJECT_AUTOMATION_TOKEN` を設定する。
2. トークンは以下いずれかを利用する。
   - Classic PAT: `repo`, `read:org`, `project`
   - Fine-grained PAT: 対象リポジトリ権限 + Organization `Projects` の read/write
3. Organization が SSO 必須の場合は、トークン作成後に `Configure SSO` で `e-llabo` を承認する。
4. 反映確認は以下コマンドで行う。
   - `gh api user`
   - `gh project view 11 --owner e-llabo --format json -q .id`
5. 失敗時の代表原因
   - `Bad credentials (401)`: トークン無効/失効
   - `Project not found`: `project` 権限不足または SSO 未承認

## 5. 親課題同期ルール

親課題の目標ステータスは、子課題集合から次の優先順位で判定する。

1. 子に `In progress` が1件でもある -> 親は `In progress`
2. 子が全件 `Done` -> 親は `Done`
3. 子に `In review` がある -> 親は `In review`
4. 子に `Approved` または `Staging / Merged to Release` がある -> 親は `Approved`
5. 子が全件 `Ready` / `Backlog` -> 親は `Ready`

補足:

- 「子課題が全て closed」の親課題は Issue 自体も close する。

## 6. Backlog移行後の補正ルール

Backlog 由来かつ未対応のチケットで、以下を満たすものは `Backlog` に戻す。

- Issue本文に `Backlog メタデータ` が存在
- メタデータ `状態=未対応`
- `関連PRなし` または `移行後コメントなし`

## 7. 2026-03-03 実施フロー（運用整備ログ）

`2026-03-03` に、以下の順で Project #11 を整備した。

1. Status オプションへ `Approved` / `Staging / Merged to Release` を追加
2. `Code review approved` workflow を有効化
3. Status 再定義で消失した値を Backlog メタデータから復元
4. `Ready` からの再分類を実施（PRあり -> `In progress`、PRありかつコメント/レビューあり -> `In review`）
5. `null` の Issue を同ルールで埋め戻し
6. `closed` の Issue/PR を `Done` に統一
7. 関連PRが `APPROVED` の Issue を `Approved` へ移動
8. 親課題を子課題状態で同期
9. 子課題が全て closed の親課題を close
10. Backlog 由来・未対応・PR未紐づき/コメントなしの Issue を `Backlog` へ戻し

## 8. 定期メンテナンス

### 週次

- `closed かつ Done 以外`、`open かつ Done` の不整合を解消
- 親課題同期を再実行
- Backlog 棚卸し（`Backlog -> Ready`）

### 月次

- Done アイテムのアーカイブ方針を確認（Project の見通し維持）

## 9. スコープ・期日・見積りの設定ルール

### 9.1 基本方針

- `Target date`（期日）は必須で持つ。
- 想定作業時間（工数日）は持つべきだが、初期は軽量運用で開始する。
- 期日は「約束と優先順位」の管理、工数は「キャパと見積り精度」の管理に使い分ける。
- `Milestone` の期日は未定でもよい（スコープ管理を優先）。
- ただし Issue の `Target date` は `Ready` 以降で必須とする。

### 9.2 設定タイミング（2段階）

1. `Backlog -> Ready` に上げる直前（Ready化ゲート）
- 必須設定: `Milestone`（スコープ）と `Target date`（期日）
- 原則: ここで未設定なら `Ready` にしない

2. `In Review` 入り時（レビューゲート）
- 必須確認: `Target date` の現実性
- 必要時更新: レビュー遅延・依存関係の変化を反映して期日を見直す

### 9.3 工数の段階導入

- フェーズ1（軽量）:
- ラベルやIssueメモで管理（例: `est:0.5d`, `est:2d`）

- フェーズ2（定着後）:
- Project Numberフィールド（例: `Estimate Days`）を追加し、数値管理へ移行

## 10. 保留・他作業待ちの表現ルール

### 10.1 基本方針

- `Status` は進捗管理に専念し、保留理由は別軸で管理する。
- 保留状態の表現のために `Status` を増やさない。

### 10.2 追加フィールド

- `Blocked`（Single Select: `Yes` / `No`）
- `Blocker Type`（Single Select）
  - `Waiting External`
  - `Waiting Other Task`
  - `Need Decision`
  - `Env/Infra`
  - `Other`
- `Blocked Since`（Date）

### 10.3 運用ルール

- 保留に入る時は `Blocked=Yes` を必須化する。
- `Status` は原則 `In progress` のまま維持し、進捗と停滞理由を分離する。
- `Blocked=Yes` の間は、`Blocker Type` と解除条件をIssueコメントへ記録する。
- 週次レビューで `Blocked=Yes` かつ `Blocked Since` が3営業日超をエスカレーション対象にする。

## 11. 起票粒度と日報連携

- すべての作業を Issue 必須にはしない（過度な起票コストを避ける）。
- ただし `> 0.5d` または仕様判断を伴う作業は Issue 必須とする。
- 軽微かつ大量発生しうる不具合は、`Bug (Light)` テンプレートで一次起票してよい（トリアージ前提）。
- 調査・設計・要件確定は、コード変更なしでも docs PR で成果を残す運用を推奨する。
- 半仕様駆動として、設計書（docs PR）なしの作業着手は原則非推奨とする。
- 例外（障害一次対応、`<= 0.5d` の軽微修正、調査スパイク）で先行着手する場合は、Issueに設計後追い期限を記載し、PRへ `design-debt` ラベルを付与する。
- 例外着手後は、後続で docs PR を必須とする。
- 軽微作業（`<= 0.5d`）を Issue 省略する場合は、PR本文に `No Issue (tiny task)` を明記する。
- 日報の成果集計は GitHub 行動履歴（Issue/PR/レビュー/コメント + tiny task PR）を対象とする。
- 設計PRと実装PRを同一Issue内で運用することは許容する（設計/実装のPR分割は各担当者判断）。

軽量バグの運用補足:

- 初期受付（`Backlog`）では、再現最小情報の収集を優先し、`Milestone` / `Target date` は後追い設定でよい。
- `Ready` 化する時点で `Milestone` / `Target date` / `Priority` を必須設定する。
- 同一症状の重複が多い場合は、親Issueを1件立てて子Issueへ集約し、個別Issueはリンク先を明示する。
- 共通原因の横断対策に関わる場合は、親Issueを推奨する（進捗可視化と対応漏れ防止のため）。ただし件数が少ない場合は単独Issue運用でもよい。

## 12. ReadyからIn progressへの移行ルール

- `Ready -> In progress` へ移す時は、関連PRを Draft で作成する（Issueリンク必須）。
- PR本文に実装方針と影響範囲を記載する。
- ブロッカーがある場合は `Blocked` 系フィールドを同時設定する。
- レビュー投入は `ready-for-review` ラベルを単独トリガーとして運用する。
- `ready-for-review` ラベル付与時に、Draft解除・レビュアー指定・`In review` 遷移を自動実行する方針とする。

## 13. 親課題構造ルール

- 階層は原則2段（親Issue -> 子Issue）までとする。
- 親課題は最初から複数子課題でなくてもよい。まず1子課題で開始する運用を許容する。
- 実行中に必要が生じた場合、後から子課題を追加して分割してよい。
- 分割時は、親Issueコメントに「分割理由」「新規子Issueリンク」「担当境界」を記録する。
- 子課題が1件のみで固定化した場合は、親課題を維持する理由（横断管理/将来分割見込み）を明記する。
- 孫課題が必要な場合は、階層を増やさず Issue間依存で表現する（疑似的な孫課題を許容）。
- 依存関係は Issue本文に `Depends on #xxxx` 形式で明記する。
- 依存待ちのIssueは `Blocked=Yes` と `Blocker Type=Waiting Other Task` を設定し、解除条件に依存Issue番号を記載する。

### 13.1 機能開発向けテンプレート運用（推奨）

- 機能を「要件調整 -> 仕様書作成 -> 実装（テスト込み）」で進める場合は、以下テンプレートの利用を推奨する（必須ではない）。
  - 親: `feature-parent.md`
  - 子: `feature-child-requirements.md` / `feature-child-specs.md` / `feature-child-implementation.md`
- 親課題は管理軸（進捗・依存・完了定義）のみを持ち、実作業は子課題で運用する。
- 規模が小さく、設計と実装を同時進行できる場合は `task.md` 1件で運用してよい。
