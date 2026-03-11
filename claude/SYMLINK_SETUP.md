# Claude Skills & Scripts セットアップガイド

このリポジトリ (`e-llabo/.github`) は Claude Code のスキル定義と実行スクリプトの正本を管理する。
各開発者のローカル環境ではシンボリックリンクを張ることで、どのプロジェクトからでもスキルを利用できる。

## ディレクトリ構成

```
e-llabo/.github/
├── .github/
│   ├── ISSUE_TEMPLATE/        ← org共通 Issue テンプレート
│   └── PULL_REQUEST_TEMPLATE.md
├── claude/
│   ├── SYMLINK_SETUP.md       ← このファイル
│   ├── skills/
│   │   ├── daily-report/
│   │   │   └── SKILL.md      ← 日報生成スキル定義
│   │   └── github-issue/
│   │       └── SKILL.md      ← Issue/Project操作スキル定義
│   └── bin/
│       ├── daily-report.py    ← 日報生成スクリプト
│       └── github-issue.py   ← Issue/Project操作スクリプト
└── README.md
```

## セットアップ手順

### 前提条件

- このリポジトリがクローン済みであること
- Claude Code がインストール済みであること

### 1. リポジトリのクローン

```bash
gh repo clone e-llabo/.github ~/dot-github
```

クローン先は任意。以降の手順では `~/dot-github` にクローンした前提で記載する。

### 2. ディレクトリ作成

```bash
mkdir -p ~/.claude/skills/daily-report
mkdir -p ~/.claude/skills/github-issue
mkdir -p ~/.claude/bin
```

### 3. シンボリックリンク作成

```bash
REPO_ROOT=~/dot-github  # ← 自分のクローン先に合わせて変更

# スキル定義
ln -sf "${REPO_ROOT}/claude/skills/daily-report/SKILL.md" ~/.claude/skills/daily-report/SKILL.md
ln -sf "${REPO_ROOT}/claude/skills/github-issue/SKILL.md" ~/.claude/skills/github-issue/SKILL.md

# 実行スクリプト
ln -sf "${REPO_ROOT}/claude/bin/daily-report.py" ~/.claude/bin/daily-report.py
ln -sf "${REPO_ROOT}/claude/bin/github-issue.py" ~/.claude/bin/github-issue.py
```

### 4. 確認

```bash
ls -la ~/.claude/skills/*/SKILL.md ~/.claude/bin/*.py
```

すべてリポジトリ内のファイルへのシンボリックリンクになっていれば完了。

## 更新方法

リポジトリ側でスキルやスクリプトを編集・マージした後、各環境で `git pull` するだけで反映される。

```bash
cd ~/dot-github && git pull
```

## 個人的なカスタマイズ

一時的にスキルやスクリプトを個人調整したい場合:

1. シンボリックリンクを削除
2. 直接ファイルを配置して編集
3. 戻す時はファイルを削除してリンクを再作成

## トラブルシューティング

| 症状 | 原因 | 対処 |
|---|---|---|
| スキルが見つからない | シンボリックリンク未設定 | 上記セットアップ手順を実行 |
| `No such file or directory` | クローン先パスが異なる | `REPO_ROOT` を正しいパスに修正して再リンク |
| スクリプト実行エラー | `git pull` 未実施 | リポジトリを最新に更新 |
