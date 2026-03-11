#!/usr/bin/env python3
"""
Backlog CSV → GitHub Issues + Project 移行スクリプト

Usage:
    python3 migrate_backlog_to_github.py --config config.json --csv Backlog-Issues.csv [--dry-run]
    python3 migrate_backlog_to_github.py --config config.json --csv Backlog-Issues.csv --link-only

See: docs/operations-guide/references/MIGRATION_GUIDE.md
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)


# ---------------------------------------------------------------------------
# gh CLI wrapper
# ---------------------------------------------------------------------------

def run_gh(args, silent=False):
    result = subprocess.run(["gh"] + args, capture_output=True, text=True)
    if result.returncode != 0:
        if not silent:
            print(f"  ERROR: {result.stderr.strip()}", flush=True)
        return None
    return result.stdout.strip()


def run_gh_json(args):
    raw = run_gh(args, silent=True)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def load_config(path):
    """config.json を読み込む"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CSV reader (Shift-JIS → UTF-8)
# ---------------------------------------------------------------------------

def read_backlog_csv(csv_path, encoding="SHIFT_JIS"):
    """Backlog エクスポート CSV を読み込み、dict のリストを返す"""
    proc = subprocess.run(
        ["iconv", "-f", encoding, "-t", "UTF-8", csv_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        print(f"ERROR: iconv failed: {proc.stderr}", flush=True)
        sys.exit(1)
    return list(csv.DictReader(proc.stdout.splitlines()))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_date(d):
    """'2026/02/04' → '2026-02-04'"""
    if not d:
        return None
    m = re.match(r"(\d{4})/(\d{2})/(\d{2})", d)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def get_existing_issues(repo):
    """リポジトリ内の既存 Issue を取得し、Backlog キーベースの辞書を返す"""
    raw = run_gh([
        "issue", "list", "--repo", repo, "--limit", "500",
        "--state", "all", "--json", "number,title,url,id,body",
    ])
    if not raw:
        return {}, {}
    issues = json.loads(raw)
    key_set = set()
    key_to_issue = {}
    for iss in issues:
        m = re.match(r"\[([A-Z0-9_]+-\d+)\]", iss["title"])
        if m:
            key = m.group(1)
            key_set.add(key)
            # 重複がある場合は番号が大きい方（後に作成された方）を優先
            if key not in key_to_issue or iss["number"] > key_to_issue[key]["number"]:
                key_to_issue[key] = iss
    return key_set, key_to_issue


def ensure_milestones(repo, milestone_names):
    """マイルストーンを作成し、title → number のマッピングを返す"""
    existing = run_gh_json(["api", f"repos/{repo}/milestones", "--jq", "."])
    ms_map = {}
    if existing and isinstance(existing, list):
        for m in existing:
            ms_map[m["title"]] = m["number"]

    for name in milestone_names:
        if name and name not in ms_map:
            result = run_gh(["api", f"repos/{repo}/milestones", "-f", f"title={name}"])
            if result:
                try:
                    d = json.loads(result)
                    ms_map[d["title"]] = d["number"]
                    print(f"  Created milestone: {name}", flush=True)
                except json.JSONDecodeError:
                    pass
    return ms_map


def get_project_id(owner, project_number):
    """Project の node ID を取得"""
    raw = run_gh([
        "project", "view", str(project_number),
        "--owner", owner, "--format", "json",
    ])
    if raw:
        data = json.loads(raw)
        return data.get("id")
    return None


def get_project_fields(owner, project_number):
    """Project のフィールド一覧を取得"""
    raw = run_gh([
        "project", "field-list", str(project_number),
        "--owner", owner, "--format", "json",
    ])
    if raw:
        return json.loads(raw)
    return None


# ---------------------------------------------------------------------------
# Issue body builder
# ---------------------------------------------------------------------------

def build_issue_body(row):
    """Backlog の行データから Issue 本文を生成"""
    key = row["キー"]
    detail = row.get("詳細", "").replace("\\n", "\n")
    parts = []

    # メタデータテーブル
    parts.append("## Backlog メタデータ\n")
    parts.append("| 項目 | 値 |")
    parts.append("|---|---|")
    parts.append(f"| Backlogキー | {key} |")
    parts.append(f"| 状態 | {row.get('状態', '')} |")
    parts.append(f"| 優先度 | {row.get('優先度', '')} |")

    optional_fields = [
        ("担当者", "担当者"),
        ("マイルストーン", "マイルストーン"),
        ("カテゴリー", "カテゴリー名"),
        ("親課題", "親課題キー"),
        ("開始日", "開始日"),
        ("期限日", "期限日"),
    ]
    for label, col in optional_fields:
        val = row.get(col, "")
        if val:
            parts.append(f"| {label} | {val} |")

    parts.append(f"| 登録者 | {row.get('登録者', '')} |")
    parts.append(f"| 登録日 | {row.get('登録日', '')} |")

    # 詳細
    if detail:
        parts.append("\n---\n")
        parts.append(detail)

    # コメント
    for c in range(1, 5):
        comment = row.get(f"コメント{c}", "").replace("\\n", "\n")
        if comment:
            parts.append(f"\n---\n### Backlog コメント{c}")
            parts.append(comment)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Sub-issue linker (GraphQL)
# ---------------------------------------------------------------------------

def link_sub_issues(repo, key_to_issue, rows, dry_run=False, delay=0.3):
    """Backlog の親課題キーを元に GitHub sub-issue 関係を設定する

    gh CLI の `--add-parent` フラグは古いバージョンでは未対応のため、
    GraphQL API の addSubIssue mutation を使用する。
    """
    # 親子ペアを抽出
    pairs = []
    seen = set()
    for row in rows:
        child_key = row["キー"]
        parent_key = row.get("親課題キー", "")
        if not parent_key:
            continue
        pair_id = f"{parent_key}->{child_key}"
        if pair_id in seen:
            continue
        seen.add(pair_id)
        if parent_key in key_to_issue and child_key in key_to_issue:
            pairs.append({
                "parent_key": parent_key,
                "parent_number": key_to_issue[parent_key]["number"],
                "parent_node_id": key_to_issue[parent_key]["id"],
                "child_key": child_key,
                "child_number": key_to_issue[child_key]["number"],
                "child_node_id": key_to_issue[child_key]["id"],
            })

    if not pairs:
        print("  No parent-child pairs found", flush=True)
        return

    print(f"  Found {len(pairs)} parent-child pairs\n", flush=True)

    success = 0
    failed = 0

    for i, p in enumerate(pairs):
        label = (
            f"[{i+1}/{len(pairs)}] "
            f"#{p['parent_number']} ({p['parent_key']}) "
            f"-> #{p['child_number']} ({p['child_key']})"
        )

        if dry_run:
            print(f"{label} [DRY-RUN]", flush=True)
            success += 1
            continue

        print(label, flush=True)

        query = """mutation($parentId: ID!, $childId: ID!) {
          addSubIssue(input: {issueId: $parentId, subIssueId: $childId}) {
            issue { id }
          }
        }"""

        result = run_gh([
            "api", "graphql",
            "-f", f"query={query}",
            "-f", f"parentId={p['parent_node_id']}",
            "-f", f"childId={p['child_node_id']}",
        ])

        if result is not None:
            print(f"  Linked", flush=True)
            success += 1
        else:
            failed += 1

        time.sleep(delay)

    print(f"\n  Sub-issue linking: Success={success}, Failed={failed}", flush=True)


# ---------------------------------------------------------------------------
# Project field setter (SingleSelect helper)
# ---------------------------------------------------------------------------

def set_single_select_field(project_id, item_id, field_ids, field_name, value_map, raw_value):
    """SingleSelect 型の Project フィールドを設定する"""
    mapped = value_map.get(raw_value)
    if not mapped or field_name not in field_ids:
        return
    f = field_ids[field_name]
    opt_id = f.get("options", {}).get(mapped)
    if opt_id:
        run_gh([
            "project", "item-edit", "--project-id", project_id, "--id", item_id,
            "--field-id", f["id"], "--single-select-option-id", opt_id,
        ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Backlog CSV → GitHub Issues + Project 移行")
    parser.add_argument("--config", required=True, help="設定ファイル (config.json)")
    parser.add_argument("--csv", required=True, help="Backlog エクスポート CSV ファイル")
    parser.add_argument("--encoding", default="SHIFT_JIS", help="CSV エンコーディング (default: SHIFT_JIS)")
    parser.add_argument("--dry-run", action="store_true", help="実際には作成せず内容を出力")
    parser.add_argument("--delay", type=float, default=0.5, help="API 呼び出し間隔 (秒)")
    parser.add_argument("--link-only", action="store_true", help="Issue作成をスキップし親子リンクのみ実行")
    args = parser.parse_args()

    # --- Load config ---
    cfg = load_config(args.config)
    repo = cfg["repo"]                            # e.g. "e-llabo/ELL_portal"
    owner = cfg["owner"]                          # e.g. "e-llabo"
    project_number = cfg["project_number"]        # e.g. 11
    assignee_map = cfg.get("assignee_map", {})    # Backlog名 → GitHub login
    status_map = cfg.get("status_map", {})        # Backlog状態 → Project Status option name
    priority_map = cfg.get("priority_map", {})    # Backlog優先度 → Project Priority option name
    category_map = cfg.get("category_map", {})    # Backlogカテゴリー → Project Category option name

    # --- Read CSV ---
    rows = read_backlog_csv(args.csv, args.encoding)
    print(f"CSV: {len(rows)} tickets loaded", flush=True)

    # --- link-only mode ---
    if args.link_only:
        print("\n=== Sub-issue linking (link-only mode) ===", flush=True)
        existing_keys, key_to_issue = get_existing_issues(repo)
        link_sub_issues(repo, key_to_issue, rows, dry_run=args.dry_run, delay=args.delay)
        return

    # --- Milestones ---
    milestone_names = sorted({r.get("マイルストーン", "") for r in rows} - {""})
    if milestone_names:
        print(f"\n=== Milestones ({len(milestone_names)}) ===", flush=True)
        if args.dry_run:
            for ms in milestone_names:
                print(f"  [DRY-RUN] Would create: {ms}", flush=True)
            ms_map = {}
        else:
            ms_map = ensure_milestones(repo, milestone_names)
            print(f"  Milestone map: {ms_map}", flush=True)
    else:
        ms_map = {}

    # --- Project info ---
    project_id = None
    field_ids = {}  # field_name → {id, options: {option_name: option_id}}

    if not args.dry_run:
        project_id = get_project_id(owner, project_number)
        if not project_id:
            print("WARNING: Could not get project ID. Project fields will not be set.", flush=True)
        else:
            fields_data = get_project_fields(owner, project_number)
            if fields_data and "fields" in fields_data:
                for f in fields_data["fields"]:
                    entry = {"id": f["id"], "type": f["type"]}
                    if "options" in f:
                        entry["options"] = {o["name"]: o["id"] for o in f["options"]}
                    field_ids[f["name"]] = entry
            print(f"  Project fields: {list(field_ids.keys())}", flush=True)

    # --- Existing issues (skip duplicates) ---
    existing_keys, key_to_issue = get_existing_issues(repo)
    print(f"  Existing issues in repo: {len(existing_keys)}", flush=True)

    # --- Create issues ---
    print(f"\n=== Creating issues ===", flush=True)
    created = 0
    skipped = 0
    failed = 0

    for i, row in enumerate(rows):
        key = row["キー"]
        title = f"[{key}] {row['件名']}"
        status = row.get("状態", "")
        priority = row.get("優先度", "")
        category = row.get("カテゴリー名", "")
        assignee_name = row.get("担当者", "")
        milestone = row.get("マイルストーン", "")
        start_date = normalize_date(row.get("開始日", ""))
        due_date = normalize_date(row.get("期限日", ""))

        # Skip existing
        if key in existing_keys:
            print(f"[{i+1}/{len(rows)}] SKIP (exists): {key}", flush=True)
            skipped += 1
            continue

        body = build_issue_body(row)

        if args.dry_run:
            print(f"\n[{i+1}/{len(rows)}] [DRY-RUN] {title[:80]}", flush=True)
            print(f"  Assignee: {assignee_map.get(assignee_name, '(none)')}", flush=True)
            print(f"  Milestone: {milestone}", flush=True)
            print(f"  Status: {status} -> {status_map.get(status, '?')}", flush=True)
            print(f"  Priority: {priority} -> {priority_map.get(priority, '?')}", flush=True)
            if category:
                print(f"  Category: {category} -> {category_map.get(category, '?')}", flush=True)
            print(f"  Start: {start_date}, Target: {due_date}", flush=True)
            created += 1
            continue

        print(f"\n[{i+1}/{len(rows)}] {key} {row['件名'][:60]}", flush=True)

        # Build gh args
        gh_args = ["issue", "create", "--repo", repo, "--title", title, "--body", body]

        gh_assignee = assignee_map.get(assignee_name)
        if gh_assignee:
            gh_args += ["--assignee", gh_assignee]

        if milestone and milestone in ms_map:
            gh_args += ["--milestone", milestone]

        issue_url = run_gh(gh_args)
        if not issue_url:
            failed += 1
            continue

        print(f"  -> {issue_url}", flush=True)
        created += 1

        # --- Add to project ---
        if not project_id:
            time.sleep(args.delay)
            continue

        item_raw = run_gh([
            "project", "item-add", str(project_number),
            "--owner", owner, "--url", issue_url, "--format", "json",
        ])
        if not item_raw:
            time.sleep(args.delay)
            continue

        try:
            item_id = json.loads(item_raw).get("id")
        except (json.JSONDecodeError, AttributeError):
            item_id = None

        if not item_id:
            time.sleep(args.delay)
            continue

        # --- Set project fields ---
        set_single_select_field(project_id, item_id, field_ids, "Status", status_map, status)
        set_single_select_field(project_id, item_id, field_ids, "Priority", priority_map, priority)
        set_single_select_field(project_id, item_id, field_ids, "Category", category_map, category)

        # Start date
        if start_date and "Start date" in field_ids:
            run_gh([
                "project", "item-edit", "--project-id", project_id, "--id", item_id,
                "--field-id", field_ids["Start date"]["id"], "--date", start_date,
            ])

        # Target date
        if due_date and "Target date" in field_ids:
            run_gh([
                "project", "item-edit", "--project-id", project_id, "--id", item_id,
                "--field-id", field_ids["Target date"]["id"], "--date", due_date,
            ])

        print(
            f"  Fields: status={status_map.get(status)}, priority={priority_map.get(priority)}, "
            f"category={category_map.get(category, '(none)')}, "
            f"start={start_date}, target={due_date}",
            flush=True,
        )
        time.sleep(args.delay)

    # --- Sub-issue linking ---
    print(f"\n=== Sub-issue linking ===", flush=True)
    # 既存 + 今回作成分を含めて再取得
    _, key_to_issue = get_existing_issues(repo)
    link_sub_issues(repo, key_to_issue, rows, dry_run=args.dry_run, delay=args.delay)

    # --- Summary ---
    print(f"\n{'=' * 40}", flush=True)
    prefix = "[DRY-RUN] " if args.dry_run else ""
    print(f"{prefix}Created: {created}, Skipped: {skipped}, Failed: {failed}", flush=True)


if __name__ == "__main__":
    main()
