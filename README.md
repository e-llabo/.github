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

### GitHub 運用ドキュメント (`docs/`)

- **github-project-operations-policy.md**: Project 運用方針（ステータス遷移・ゲートチェック）
- **github-issue-creation-guide.md**: Issue 起票ガイドライン
- **github-project-built-in-workflow-recommendations.md**: GitHub Project Workflow 推奨設定
- **references/**: Backlog → GitHub 移行ツール・ガイド
