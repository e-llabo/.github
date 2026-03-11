# Backlog → GitHub Issues + Project 移行手順書

Backlog のチケットを GitHub Issues として登録し、GitHub Project に追加するための手順。

## 前提条件

- `gh` CLI がインストール・認証済みであること
- `python3` (3.8+) が利用可能であること
- `iconv` コマンドが利用可能であること（Linux/macOS 標準搭載）

## 事前準備

### 1. gh CLI の認証スコープ追加

Project の読み書きには追加スコープが必要。

```bash
# 読み取り + 書き込みスコープを追加（ブラウザ認証が開く）
gh auth refresh -s project -h github.com
```

### 2. GitHub Project の作成

テンプレートからコピーする場合:

```bash
gh project copy <TEMPLATE_NUMBER> \
  --source-owner <ORG> \
  --target-owner <ORG> \
  --title "プロジェクト名" \
  --format json
```

新規作成する場合:

```bash
gh project create --owner <ORG> --title "プロジェクト名" --format json
```

返却される `number` を控えておく。

### 3. Backlog から CSV エクスポート

1. Backlog プロジェクト → 課題一覧 → フィルタで対象を絞り込み
2. 右上の「Excel にエクスポート」→ CSV 形式でダウンロード
3. エンコーディングは **Shift-JIS** （デフォルト）

### 4. 設定ファイルの作成

`config.example.json` をコピーして編集する。

```bash
cp config.example.json config.json
```

#### config.json の項目

| キー | 説明 | 例 |
|---|---|---|
| `repo` | GitHub リポジトリ (`owner/repo`) | `"e-llabo/ELL_portal"` |
| `owner` | Organization 名 | `"e-llabo"` |
| `project_number` | Project 番号 | `11` |
| `assignee_map` | Backlog 担当者名 → GitHub login | `{"山田太郎": "taro-yamada"}` |
| `status_map` | Backlog 状態 → Project Status オプション名 | `{"未対応": "Backlog"}` |
| `priority_map` | Backlog 優先度 → Project Priority オプション名 | `{"高": "P1"}` |
| `category_map` | Backlog カテゴリー名 → Project Category オプション名 | `{"バックエンド": "BE"}` |

> **Note:** `category_map` は Backlog CSV の「カテゴリー名」列が空でない場合にのみ有効。
> カテゴリーが不要なプロジェクトでは空オブジェクト `{}` でよい。

#### assignee_map の確認方法

```bash
# Organization メンバーの GitHub login 一覧
gh api orgs/<ORG>/members --jq '.[].login'
```

#### status_map / priority_map / category_map の確認方法

```bash
# Project のフィールドとオプション一覧
gh project field-list <PROJECT_NUMBER> --owner <ORG> --format json
```

出力例:
```json
{
  "name": "Status",
  "options": [
    {"id": "f75ad846", "name": "Ready"},
    {"id": "47fc9ee4", "name": "In Progress"},
    {"id": "df73e18b", "name": "In Review"},
    {"id": "98236657", "name": "Approved"},
    {"id": "a123b456", "name": "Staging / Merged to Release"}
  ]
}
```

各マップの **値** にはここの `name` を指定する（`id` はスクリプトが自動解決する）。

#### 推奨ステータス設計（運用）

GitHub Projects の Status は、以下の 5 段階を推奨する。

| Status | 意味 |
|---|---|
| `Ready` | 着手可能（未着手） |
| `In Progress` | 実装中 |
| `In Review` | レビュー中 |
| `Approved` | レビュー通過・マージ待ち |
| `Staging / Merged to Release` | リリースブランチへマージ済み（本番未反映） |

`Approved` と `Staging / Merged to Release` を分けることで、レビュー後の滞留と本番反映待ちのギャップを可視化できる。

#### Built-in workflows: PR Approved 時に `Approved` へ自動移動

GitHub Projects の Built-in workflows を使い、Pull Request の承認時に関連 Issue の Status を自動更新する。

1. 対象 Project を開き、`Workflows` を開く
2. `Built-in workflows` から Pull Request レビューイベントに紐づくワークフローを追加
3. Trigger を `Pull request reviewed` + `Review state: Approved` に設定
4. Action を `Set field` にし、`Status = Approved` を設定
5. 保存して有効化する

運用上の注意:

- 自動遷移対象は、Project に追加済みのアイテムかつ PR と Issue がリンクしているもの（例: `Closes #123`）に限られる。
- `Approved` は「開発者の手を離れたが未マージ」の状態であり、マージ後は別ワークフローで `Staging / Merged to Release` へ遷移させる。

## 実行

### ステップ1: ドライラン（実際には作成しない）

```bash
python3 migrate_backlog_to_github.py \
  --config config.json \
  --csv Backlog-Issues-YYYYMMDD.csv \
  --dry-run
```

マッピングの確認ができる。問題なければ本番実行へ。

### ステップ2: 本番実行

```bash
python3 migrate_backlog_to_github.py \
  --config config.json \
  --csv Backlog-Issues-YYYYMMDD.csv
```

本番実行では以下が自動で行われる:

1. マイルストーンの作成
2. Issue の作成（タイトル・本文・担当者・マイルストーン）
3. Project への追加とフィールド設定（Status / Priority / Category / Start date / Target date）
4. **親子課題の sub-issue リンク設定**（Backlog の「親課題キー」を元に自動リンク）

#### オプション

| フラグ | 説明 | デフォルト |
|---|---|---|
| `--encoding` | CSV エンコーディング | `SHIFT_JIS` |
| `--delay` | API 呼び出し間隔（秒） | `0.5` |
| `--dry-run` | 実行せず内容を出力 | `false` |
| `--link-only` | Issue作成をスキップし親子リンクのみ実行 | `false` |

### ステップ3（任意）: 親子リンクのみ再実行

Issue は作成済みだが親子リンクだけやり直したい場合:

```bash
python3 migrate_backlog_to_github.py \
  --config config.json \
  --csv Backlog-Issues-YYYYMMDD.csv \
  --link-only
```

### 中断・再実行

- **Issue 作成**: タイトルの Backlog キー (`[AUBEQUEST1F-xxx]`) で重複チェックを行うため、途中で中断しても再実行すれば作成済みをスキップして残りから再開する。
- **親子リンク**: 既にリンク済みの場合は GraphQL API がエラーを返すが、処理は継続する。

## 移行される属性の一覧

### GitHub Issue に設定される属性

| Backlog 項目 | GitHub Issue 属性 | 備考 |
|---|---|---|
| キー + 件名 | Title | `[KEY] 件名` 形式 |
| 詳細 + メタデータ | Body | メタデータテーブル + 詳細本文 + コメント |
| 担当者 | Assignee | `assignee_map` で変換 |
| マイルストーン | Milestone | 未作成なら自動作成 |
| 親課題キー | **Sub-issue** | GraphQL `addSubIssue` で親子リンク |

### GitHub Project に設定される属性

| Backlog 項目 | Project フィールド | 備考 |
|---|---|---|
| 状態 | Status | `status_map` で変換（SingleSelect） |
| 優先度 | Priority | `priority_map` で変換（SingleSelect） |
| カテゴリー名 | Category | `category_map` で変換（SingleSelect） |
| 開始日 | Start date | `YYYY/MM/DD` → `YYYY-MM-DD` 自動変換 |
| 期限日 | Target date | 同上 |
| （自動） | Parent issue | sub-issue リンクから自動反映 |
| （自動） | Sub-issues progress | sub-issue リンクから自動反映 |

### Issue Body に保存される属性（参照用）

上記に加え、以下の情報が Issue 本文のメタデータテーブルに記録される。

- Backlog キー
- 親課題キー
- カテゴリー名
- 登録者・登録日
- Backlog コメント（最大4件）

### Backlog CSV に存在するが移行対象外の項目

| Backlog 項目 | 理由 |
|---|---|
| 種別 | 全件同一値（タスク）になることが多い |
| 発生バージョン | 空であることが多い |
| 完了理由 | 空であることが多い |
| 予定時間 / 実績時間 | GitHub Project に対応フィールドがない |
| 添付 / 共有 | Backlog CSV にはファイル実体が含まれない |

> 必要に応じてスクリプトを拡張し、ラベルや追加のカスタムフィールドにマッピング可能。

## 注意事項

- **config.json に個人名を含む場合**: `assignee_map` に実名が入るため、リポジトリにコミットする際は `config.example.json` のようにダミー値にする。実際の `config.json` は `.gitignore` に追加するか、手元のみで管理する。
- **gh CLI のバージョン**: sub-issue リンクは GraphQL API (`addSubIssue` mutation) を使用する。`gh issue edit --add-parent` は gh v2.83 時点では未対応だったため採用していない。

## トラブルシューティング

### `error: your authentication token is missing required scopes`

```bash
gh auth refresh -s project -h github.com
```

### Rate limit に引っかかる

`--delay` の値を大きくする（例: `--delay 2`）。

### CSV の文字化け

Backlog のデフォルトは Shift-JIS。UTF-8 の場合は `--encoding UTF-8` を指定。

### Milestone の作成に失敗

リポジトリの write 権限があるか確認:

```bash
gh api repos/<OWNER>/<REPO> --jq '.permissions'
```

### Sub-issue リンクが失敗する

GraphQL API の `addSubIssue` が使えない場合、リポジトリ設定で sub-issues 機能が有効か確認。
リンクのみ再実行するには `--link-only` を使用。

## ファイル構成

```
docs/operations-guide/references/
  MIGRATION_GUIDE.md              # 本手順書
  migrate_backlog_to_github.py    # 移行スクリプト
  config.example.json             # 設定ファイルテンプレート
```
