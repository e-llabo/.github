---
title: GitHub Issue起票ガイド（Project運用準拠）
description: Project #11 の運用方針に沿って、Issue起票時に設定すべき必須項目とステータス運用ルールを定義する
tags: [運用, GitHub Issues, GitHub Projects, 起票ルール]
---

# GitHub Issue起票ガイド（Project運用準拠）

## 1. 目的

- 起票品質を揃え、`Ready` 化と優先度判断を速くする。
- `Status`、`Blocked`、親子課題同期の運用崩れを防ぐ。

## 2. 起票テンプレート

- テンプレート: `.github/ISSUE_TEMPLATE/task.md`
- 対象: 実装・調査・運用タスク全般
- テンプレート: `.github/ISSUE_TEMPLATE/bug-light.md`
- 対象: 軽微かつ大量発生しうる不具合の一次起票（トリアージ前提）
- テンプレート: `.github/ISSUE_TEMPLATE/feature-parent.md`
- 対象: 1機能を横断管理する親Issue（要件/仕様/実装を束ねる）
- テンプレート: `.github/ISSUE_TEMPLATE/feature-child-requirements.md`
- 対象: 要件調整の子Issue
- テンプレート: `.github/ISSUE_TEMPLATE/feature-child-specs.md`
- 対象: 仕様書作成の子Issue
- テンプレート: `.github/ISSUE_TEMPLATE/feature-child-implementation.md`
- 対象: 実装（テスト込み）の子Issue

成果物リポジトリの原則:

- docs はモノレポ管理を原則とする。
- ただし、複雑な画面デザイン、プレゼン資料、他領域横断のインフラ作業は専用リポジトリで成果管理してよい。
- 専用リポジトリを使う場合は、関連Issueにリポジトリ/PRリンクを必ず残す。

### 2.1 テンプレート使い分け（機能開発）

- 1機能を「要件調整 -> 仕様書作成 -> 実装（テスト込み）」で進める場合は、親Issue + 子Issueテンプレートの利用を推奨する（必須ではない）。
- 設計と実装を同時進行できる小中規模作業は、従来どおり `task.md` を1件で使ってよい。
- 親Issueは進捗管理と依存可視化を主目的とし、実作業・受け入れ条件は子Issue側で具体化する。

## 3. 起票時の必須項目

Issue本文（テンプレート）で必須:

- 概要
- 完了条件（受け入れ条件）
- スコープ（対象/対象外）
- 期日（`Target date`）

Project項目で必須:

- `Milestone`
- `Target date`
- `Status`（初期値は `Backlog` または `Ready`）

補足:

- `Milestone` の期日（Due date）は未定でも可（スコープ管理を優先）
- Issue の `Target date` は `Ready` 以降で必須

軽量バグ（`Bug (Light)`）の例外:

- 初期受付（`Backlog`）時は `Milestone` / `Target date` の設定を必須化しない。
- `Backlog -> Ready` に上げるタイミングで `Milestone` / `Target date` / `Priority` を必須設定とする。
- 受付時は「期待動作/実際の動作」「最小再現情報」「影響と緊急度（一次判定）」を優先する。

## 3.1 Issue起票の適用範囲（必須/任意）

Issue必須:

- 実装PR（コード変更）
- 設計（docs PR）で意思決定を残すもの
- 調査で半日超、または複数人連携が必要なもの
- 定期メンテでも再発防止・恒久対応が絡むもの

成果物の推奨:

- 調査、設計、要件確定は、コード変更がなくても docs PR を成果物として作成することを推奨する
- 半仕様駆動として、設計書（docs PR）なしの作業着手は原則非推奨とする
- 例外（障害一次対応、`<= 0.5d` の軽微修正、調査スパイク）で先行着手する場合は、設計後追い期限をIssueへ記載し、PRに `design-debt` ラベルを付与する
- 例外着手後は docs PR を後続必須とする
- 設計PRと実装PRを同一Issue内で運用してよい（PR分割は担当者判断）

Issue任意（PR直行可）:

- 30分以内の軽微修正
- 誤字修正、リンク修正、体裁調整のみ
- 既存Issueのサブタスクとして完結する小作業

運用基準:

- `<= 0.5d` かつ単独作業は Issue 省略可
- `> 0.5d` または仕様判断ありは Issue 必須
- 共通原因に対する横断対策（複数Issueをまたぐ恒久対応・監視強化・共通修正）が必要な場合は、親Issueを作成して子Issueを紐づける運用を推奨する（必須ではない）。

## 4. ステータス運用

- `Backlog`: 未着手（棚卸し・優先度待ち）
- `Ready`: 着手可能（Milestone/期日が揃っている）
- `In progress`: 実作業中
- `In review`: PRあり + レビュー活動あり
- `Approved`: 関連PRが承認済み
- `Staging / Merged to Release`: リリースブランチ反映済み・本番未反映
- `Done`: 課題完了

## 5. 期日と見積り

- `Target date` は必須。
- 見積りは段階導入とし、初期は Issue本文で `est:*d` 形式を使う。
  - 例: `est:0.5d`, `est:2d`

## 6. 保留（Blocked）運用

- 保留時は `Blocked=Yes` を必須化。
- `Blocker Type` と `Blocked Since` を設定する。
- 解除条件を Issueコメントへ記載する。
- `Status` は原則 `In progress` のまま維持する（進捗と停滞理由を分離）。

## 7. Ready化ゲート

`Backlog -> Ready` に上げる前に、以下を満たすこと。

- `Milestone` 設定済み
- `Target date` 設定済み
- 受け入れ条件が記載済み
- 依存関係が明示済み

## 8. In Reviewゲート

`In Review` へ上げる時に、以下を確認する。

- 関連PRが存在し、Draft解除済みである
- 期日（`Target date`）の現実性を再確認した
- 依存Issue（`Depends on`）がある場合、レビュー着手可能な状態である

レビュー投入トリガー:

- コメント運用は使わず、`ready-for-review` ラベル単独で運用する。
- 同ラベル付与を起点に、Draft解除・レビュアー指定・`In review` 遷移を実行する。

## 9. In Progress化ゲート

`Ready -> In progress` に上げる時に、以下を満たすこと。

- 関連PRを **Draft** で作成済み（Issueリンク必須）
- 実装方針・影響範囲をPR本文へ記載済み
- 着手時点の作業分解（最初の1-3ステップ）をIssueコメントで宣言済み
- ブロッカーがある場合は `Blocked=Yes` / `Blocker Type` / `Blocked Since` を設定済み

親子課題の運用補足:

- 親課題は1子課題で開始してよい
- 実施中に必要になれば、後から子課題追加（分割）してよい
- 孫課題は作らず、必要な連鎖は Issue間依存（`Depends on #xxxx`）で表現する
- 依存待ちは `Blocked=Yes` + `Blocker Type=Waiting Other Task` を設定する

## 10. 参照

- `docs/operations-guide/github-project-operations-policy.md`
- `docs/operations-guide/references/MIGRATION_GUIDE.md`

## 11. 日報連携（GitHub行動履歴ベース）

日報で成果集計する前提のため、Issue省略時は以下を必須化する。

- PR本文に `No Issue (tiny task)` を明記する
- 可能なら PRに `tiny-task` ラベルを付与する

日報集計対象:

- Issue作成/更新
- PR作成/更新/マージ
- PRレビュー/コメント
- Issueコメント
- `No Issue (tiny task)` PR
