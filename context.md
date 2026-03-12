# コンテキスト引き継ぎ資料

## このフォルダについて

GitHub組織運用ルール整備プロジェクトの設計・計画・引き継ぎ資料一式。
作業対象は `e-llabo/.github` リポジトリであり、麻雀リポ（mahjong_unity）ではない。

## フォルダ構成

```
github-org-setup/
├── context.md          # 本ファイル（コンテキスト引き継ぎ）
├── design.md           # 設計書（確定済み）
├── plan.md             # 実装計画書
└── team-share.md       # チーム共有用テキスト
```

## 背景

麻雀プロジェクトでの作業中に、組織全体のGitHub運用が整備されていないことが課題として浮上。
Issue命名の不統一、ラベル未使用、運用ルール未明文化を解決するため、`.github`リポに以下を整備する。

## 決定事項

### 方針
- Issue作成はClaude Code中心（`/create-issue`スキル）
- Web UIテンプレートはフォールバック
- テンプレートはYAML form形式（`.yml`）
- 全プロジェクト共通のベーシック構成（技術スタック非依存）
- ポータルリポの既存テンプレートを参考に簡素化

### `.github`リポの最終構成
```
e-llabo/.github/
├── README.md                          # 組織共通GitHub運用ルール
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── config.yml                 # 白紙Issue禁止
│   │   ├── bug.yml                    # [BUG] バグ報告
│   │   ├── feature.yml                # [FEAT] 新機能
│   │   └── task.yml                   # [TASK] 汎用タスク
│   └── PULL_REQUEST_TEMPLATE.md       # PRテンプレート
├── labels.yml                         # 組織共通ラベル定義
└── skills/                            # 組織共通Claude Codeスキル
    ├── create-issue.md                # Issue作成スキル
    ├── daily-report.md                # 日報自動生成
    └── work-history.md                # 作業履歴調査（将来）
```

### ラベル（確定）
種別: `epic` / `feature` / `bug` / `task` / `docs` / `planning` / `marketing` / `design`
フェーズ: `requirements` / `implementation`
運用: `blocked` / `ready-for-review` / `design-debt` / `tiny-task`

### ブランチ命名（確定）
`{type}/issue-{番号}-{短い説明}`

### ProjectV2ステータス（既存を継続）
`Backlog` → `Ready` → `In progress` → `In review` → `Done`

## 作業対象リポ

- **メイン作業**: `e-llabo/.github`（ローカル: `/tmp/.github-org` にclone済み）
- **既存参考**: ポータルリポ（WSL: `/home/deploy/ELL_portal/.github/`）
- **副次作業**: `mahjong_unity` の `.claude/commands/daily-report.md` を削除

## `.github`リポの現状

- 既に存在（`gh repo view e-llabo/.github` で確認済み）
- 中身は `README.md`（1行）と `.github/ISSUE_TEMPLATE/bug_report.yml`（英語版）のみ
- `bug_report.yml` は新しい `bug.yml` で置き換える

## 実装の優先順位

| 優先度 | 成果物 | 内容 |
|--------|--------|------|
| 1 | テンプレート・ラベル・README | `.github`リポ基盤整備 |
| 2 | `create-issue`スキル | Issue作成の自動化 |
| 3 | `daily-report`スキル | 一本化・`.github`リポに移動 |
| 4 | ラベル同期・検証 | 主要リポへのラベル適用、テスト運用開始 |

## 実装時の注意

- `.github`リポの場合、テンプレートは `.github/.github/ISSUE_TEMPLATE/` に配置（リポ名自体が`.github`のため）
- 既存の `bug_report.yml` は `git rm` してから新しい `bug.yml` を作成
- daily-reportスキルは `~/.claude/skills/daily-report.md` の内容をベースにする
- `create-issue`スキルは新規作成

## 参照リンク

- 設計書: `design.md`
- 実装計画: `plan.md`
- ポータルのテンプレート: WSL `/home/deploy/ELL_portal/.github/`
- daily-report既存スキル: `~/.claude/skills/daily-report.md`
