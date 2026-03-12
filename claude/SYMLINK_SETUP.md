# Claude Skills & Scripts セットアップガイド

このリポジトリ (`e-llabo/.github`) は Claude Code のスキル定義と実行スクリプトの **正本** を管理する。
各開発者のローカル環境にシンボリックリンクを張ることで、どのプロジェクトからでもスキルを利用できる。

## 仕組み

```
e-llabo/.github (このリポ = 正本)
  └── claude/
      ├── bin/github-issue.py       ← 実行エンジン
      ├── skills/github-issue/      ← スキル定義
      └── setup.sh                  ← セットアップスクリプト
            │
            ▼  symlink
~/.claude/ (Claude Code が自動参照する場所)
  ├── bin/github-issue.py         → 正本へのリンク
  └── skills/github-issue/SKILL.md → 正本へのリンク
            │
            ▼  どのリポで Claude Code を開いても
~/projects/AUBE_mahjong_project/   ← ここで「issue作りたい」→ スキル発動
~/projects/ELL_portal/             ← ここでも同じスキルが使える
```

## 環境ごとのセットアップ

### Windows と WSL を併用する場合

Windows と WSL はファイルシステムが別なので、**それぞれ個別にセットアップ** する。

| 環境 | 対象作業 | `~/.claude/` の場所 |
|---|---|---|
| Windows (Git Bash / PowerShell) | 麻雀フロント (Unity) 等 | `C:\Users\<user>\.claude\` |
| WSL | Portal, 麻雀バックエンド等 | `/home/<user>/.claude/` |

両方で Claude Code を使う場合は、**両方でセットアップが必要**。

---

## セットアップ手順

### 前提条件

- `gh` CLI がインストール・認証済みであること
- Claude Code がインストール済みであること
- Python 3.10 以上がインストール済みであること

### 1. リポジトリのクローン

**Windows (Git Bash):**
```bash
gh repo clone e-llabo/.github ~/dot-github
```

**WSL:**
```bash
gh repo clone e-llabo/.github ~/dot-github
```

クローン先は任意。両方の環境で使う場合は **それぞれの環境内にクローン** する。
（WSL から `/mnt/c/...` を参照する方式はパフォーマンスが悪いため非推奨）

### 2. セットアップスクリプトの実行

```bash
cd ~/dot-github && bash claude/setup.sh
```

これだけで完了。以下が自動で行われる:

1. `~/.claude/bin/` と `~/.claude/skills/*/` ディレクトリの作成
2. 全スキル・全スクリプトのシンボリックリンク作成
3. リンクの検証

### 3. 確認

```bash
ls -la ~/.claude/skills/*/SKILL.md ~/.claude/bin/*.py
```

すべてリポジトリ内のファイルへのシンボリックリンクになっていれば OK。

---

## 更新方法

リポジトリ側でスキルやスクリプトが更新された場合、`git pull` するだけで反映される。

```bash
cd ~/dot-github && git pull
```

新しいスキルが追加された場合は `setup.sh` を再実行する:

```bash
cd ~/dot-github && bash claude/setup.sh
```

既存のリンクは上書きされるだけなので、何度実行しても安全。

---

## ディレクトリ構成

```
e-llabo/.github/
├── .github/
│   ├── ISSUE_TEMPLATE/            ← org共通 Issue テンプレート
│   └── PULL_REQUEST_TEMPLATE.md
├── claude/
│   ├── setup.sh                   ← セットアップスクリプト
│   ├── SYMLINK_SETUP.md           ← このファイル
│   ├── skills/
│   │   ├── daily-report/
│   │   │   └── SKILL.md           ← 日報生成スキル定義
│   │   └── github-issue/
│   │       └── SKILL.md           ← Issue/Project操作スキル定義
│   └── bin/
│       ├── daily-report.py        ← 日報生成スクリプト
│       └── github-issue.py        ← Issue/Project操作スクリプト
├── docs/                          ← 運用ドキュメント
└── README.md
```

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| スキルが見つからない | シンボリックリンク未設定 | `setup.sh` を実行 |
| WSLで使えるがWindowsで使えない | Windows側が未セットアップ | Windows側でも別途 `setup.sh` を実行 |
| `python3: command not found` (Windows) | Windows の python3 は App Store スタブ | `python` を使用（setup.sh は自動対応済み） |
| `UnicodeDecodeError: cp932` | Windows の文字コード問題 | github-issue.py は対応済み。`git pull` で最新版に更新 |
| `No such file or directory` | クローン先パスが異なる | `setup.sh` はスクリプト自身の位置から自動解決するので再実行で解決 |
| スクリプトが古い | `git pull` 未実施 | `cd ~/dot-github && git pull` |
