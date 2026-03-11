# .github

e-llabo organization の共通設定リポジトリ。

## 内容

### GitHub テンプレート (`.github/`)

- **Issue テンプレート**: task, bug-light, feature-parent, feature-child-*
- **PR テンプレート**: 仕様駆動の開発フローに対応

各リポジトリに個別テンプレートがない場合、ここのテンプレートが自動適用される。

### Claude Code スキル & スクリプト (`claude/`)

- **daily-report**: GitHub org 全体の活動を収集し HTML 日報を生成
- **github-issue**: Issue 起票・ステータス遷移・ブロック管理・親子課題同期を自動化

セットアップ手順は [claude/SYMLINK_SETUP.md](claude/SYMLINK_SETUP.md) を参照。

### GitHub Actions (`.github/workflows/`, `.github/scripts/`)

- **project_status_sync.yml**: PR/Issue イベントに連動した Project ステータス自動遷移
- **project_parent_sync.yml**: 親子課題のステータス同期（日次スケジュール）

> **注意**: `.github` リポに置いたワークフローは、このリポ自体のイベントにしか発火しない。
> 各リポ（ELL_portal 等）で動作させるには、以下のいずれかの対応が必要:
>
> 1. **各リポにもワークフローを配置する**（現状の運用。このリポを正本として同期管理）
> 2. **Reusable workflow として呼び出す** — 各リポのワークフローから `uses: e-llabo/.github/.github/workflows/project_status_sync.yml@main` で参照（推奨移行先）
>
> スクリプト（`.github/scripts/`）は reusable workflow と組み合わせる場合、呼び出し元リポに checkout が必要な点に注意。

### GitHub 運用ドキュメント (`docs/`)

- **github-project-operations-policy.md**: Project 運用方針（ステータス遷移・ゲートチェック）
- **github-issue-creation-guide.md**: Issue 起票ガイドライン
- **github-project-built-in-workflow-recommendations.md**: GitHub Project Workflow 推奨設定
- **github-project-setup-guide.md**: Project 新規作成手順（フィールド定義・Actions連携・トークン設定）
- **references/**: Backlog → GitHub 移行ツール・ガイド
