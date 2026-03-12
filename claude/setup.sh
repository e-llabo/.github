#!/usr/bin/env bash
set -euo pipefail

# =============================================================
# Claude Skills & Scripts セットアップスクリプト
# Windows (Git Bash) / WSL / macOS / Linux 対応
#
# 使い方:
#   cd <このリポのクローン先> && bash claude/setup.sh
# =============================================================

# --- スクリプト自身の位置からリポルートを自動解決 ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CLAUDE_DIR="${HOME}/.claude"
SKILLS_SRC="${REPO_ROOT}/claude/skills"
BIN_SRC="${REPO_ROOT}/claude/bin"

echo "=================================="
echo " Claude Skills セットアップ"
echo "=================================="
echo ""
echo "リポジトリ:  ${REPO_ROOT}"
echo "リンク先:    ${CLAUDE_DIR}"
echo ""

# --- OS検出 ---
OS="unknown"
if [[ "$(uname -s)" == "Linux" ]]; then
    if grep -qi microsoft /proc/version 2>/dev/null; then
        OS="wsl"
    else
        OS="linux"
    fi
elif [[ "$(uname -s)" == "Darwin" ]]; then
    OS="mac"
elif [[ "$(uname -s)" == MINGW* ]] || [[ "$(uname -s)" == MSYS* ]]; then
    OS="windows"
fi
echo "検出された環境: ${OS}"
echo ""

# --- ディレクトリ作成 ---
echo "[1/3] ディレクトリ作成..."
mkdir -p "${CLAUDE_DIR}/bin"

# skills 配下のサブディレクトリを自動検出
for skill_dir in "${SKILLS_SRC}"/*/; do
    if [ -d "${skill_dir}" ]; then
        skill_name="$(basename "${skill_dir}")"
        mkdir -p "${CLAUDE_DIR}/skills/${skill_name}"
    fi
done

echo "  OK"
echo ""

# --- シンボリックリンク作成 ---
echo "[2/3] シンボリックリンク作成..."

link_count=0

# スキル定義 (SKILL.md)
for skill_dir in "${SKILLS_SRC}"/*/; do
    if [ -d "${skill_dir}" ]; then
        skill_name="$(basename "${skill_dir}")"
        src="${skill_dir}SKILL.md"
        dest="${CLAUDE_DIR}/skills/${skill_name}/SKILL.md"
        if [ -f "${src}" ]; then
            ln -sf "${src}" "${dest}"
            echo "  skills/${skill_name}/SKILL.md -> OK"
            link_count=$((link_count + 1))
        fi
    fi
done

# 実行スクリプト (*.py)
for script in "${BIN_SRC}"/*.py; do
    if [ -f "${script}" ]; then
        script_name="$(basename "${script}")"
        ln -sf "${script}" "${CLAUDE_DIR}/bin/${script_name}"
        echo "  bin/${script_name} -> OK"
        link_count=$((link_count + 1))
    fi
done

echo ""

# --- 検証 ---
echo "[3/3] 検証..."

error_count=0

# セットアップ対象のスキルのみ検証（SKILLS_SRC にあるもの）
for skill_dir in "${SKILLS_SRC}"/*/; do
    if [ -d "${skill_dir}" ]; then
        skill_name="$(basename "${skill_dir}")"
        dest="${CLAUDE_DIR}/skills/${skill_name}/SKILL.md"
        if [ -f "${dest}" ]; then
            echo "  skills/${skill_name}/SKILL.md ... OK"
        else
            echo "  skills/${skill_name}/SKILL.md ... NG"
            error_count=$((error_count + 1))
        fi
    fi
done

for script in "${BIN_SRC}"/*.py; do
    if [ -f "${script}" ]; then
        script_name="$(basename "${script}")"
        dest="${CLAUDE_DIR}/bin/${script_name}"
        if [ -f "${dest}" ]; then
            echo "  bin/${script_name} ... OK"
        else
            echo "  bin/${script_name} ... NG"
            error_count=$((error_count + 1))
        fi
    fi
done

echo ""
echo "=================================="
if [ ${error_count} -eq 0 ]; then
    echo " セットアップ完了 (${link_count} リンク作成)"
else
    echo " 完了 (エラー: ${error_count}件)"
fi
echo "=================================="
echo ""

# --- 環境別の注意事項 ---
if [[ "${OS}" == "windows" ]]; then
    echo "注意 (Windows):"
    echo "  - WSLでも Claude Code を使う場合は、WSL内でも別途セットアップしてください"
    echo "  - python3 ではなく python を使用してください"
    echo ""
elif [[ "${OS}" == "wsl" ]]; then
    echo "注意 (WSL):"
    echo "  - Windowsでも Claude Code を使う場合は、Windows側 (Git Bash) でも別途セットアップしてください"
    echo ""
fi
