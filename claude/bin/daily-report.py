#!/usr/bin/env python3
"""GitHub日報ジェネレーター - GitHub + Backlog の活動をHTML日報として生成する"""

import subprocess
import json
import sys
import os
import re
import argparse
import webbrowser
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
from html import escape


def load_env_from_bashrc():
    """環境変数が未設定の場合、~/.bashrc の export 行から補完する"""
    bashrc = Path.home() / ".bashrc"
    if not bashrc.exists():
        return
    try:
        for line in bashrc.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            m = re.match(r'^export\s+([A-Za-z_][A-Za-z_0-9]*)=["\']?(.*?)["\']?\s*$', line)
            if m:
                key, val = m.group(1), m.group(2)
                if key not in os.environ or not os.environ[key]:
                    os.environ[key] = val
    except OSError:
        pass


# スクリプト読み込み時に一度だけ実行
load_env_from_bashrc()


def run_gh(args, allow_empty=False):
    """ghコマンドを実行してstdoutを返す"""
    result = subprocess.run(
        ["gh"] + args, capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        if allow_empty:
            return ""
        print(f"Error: gh {' '.join(args)}\n{result.stderr}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def run_gh_json(args, allow_empty=False):
    """ghコマンドを実行してJSONをパースして返す"""
    output = run_gh(args, allow_empty=allow_empty)
    if not output:
        return [] if allow_empty else None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return [] if allow_empty else None


def get_username():
    """現在のGitHubユーザー名を取得"""
    return run_gh(["api", "user", "--jq", ".login"])


def get_org_members(org):
    """orgのメンバーリストを取得する"""
    members = run_gh_json(
        ["api", f"orgs/{org}/members", "--paginate", "--jq", "[.[].login]"],
        allow_empty=True,
    )
    if members and isinstance(members, list):
        return sorted(members)
    return []


def get_repo_info():
    """カレントリポジトリのowner/name/defaultBranchを取得"""
    data = run_gh_json(["repo", "view", "--json", "owner,name,defaultBranchRef"])
    if data:
        default_branch = data.get("defaultBranchRef", {}).get("name", "main")
        return data["owner"]["login"], data["name"], default_branch
    return None, None, None


def get_current_branch():
    """カレントブランチ名を取得"""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def extract_issue_numbers(text):
    """テキストからIssue番号を抽出する (#123 形式)"""
    if not text:
        return []
    return list(set(re.findall(r"#(\d+)", text)))


# ============================================================
# org全リポ Discovery
# ============================================================

def get_active_repos(org, username, date_str):
    """Search APIでorg内のユーザー活動があるリポジトリを特定する

    コミット / PR / レビュー / コメント / Issue作成 の5種の検索で
    活動のあったリポジトリを重複排除で収集し、default_branch情報付きで返す。
    """
    repos = set()

    search_queries = [
        # コミット
        ("commits", f"author:{username}+org:{org}+committer-date:{date_str}..{date_str}"),
        # PR作成/更新
        ("issues", f"is:pr+author:{username}+org:{org}+updated:{date_str}..{date_str}"),
        # レビュー
        ("issues", f"is:pr+reviewed-by:{username}+org:{org}+updated:{date_str}..{date_str}"),
        # コメント
        ("issues", f"commenter:{username}+org:{org}+updated:{date_str}..{date_str}"),
        # Issue作成
        ("issues", f"is:issue+author:{username}+org:{org}+created:{date_str}..{date_str}"),
    ]

    for endpoint, query in search_queries:
        url = f"/search/{endpoint}?q={query}&per_page=100"
        data = run_gh_json(["api", url], allow_empty=True)
        if not data or not isinstance(data, dict):
            continue
        for item in data.get("items", []):
            if endpoint == "commits":
                full_name = item.get("repository", {}).get("full_name", "")
            else:
                repo_api_url = item.get("repository_url", "")
                full_name = (
                    repo_api_url.replace("https://api.github.com/repos/", "")
                    if repo_api_url else ""
                )
            if full_name:
                repos.add(full_name)

    # 各リポのdefault_branchを取得
    result = []
    for full_name in sorted(repos):
        parts = full_name.split("/", 1)
        if len(parts) != 2:
            continue
        owner, repo = parts
        default_branch = run_gh(
            ["api", f"/repos/{full_name}", "--jq", ".default_branch"],
            allow_empty=True,
        ) or "main"
        result.append((owner, repo, default_branch))

    return result


# ============================================================
# データ収集
# ============================================================

def get_commits(owner, repo, username, date_str, branches=None):
    """指定日のコミットを全ブランチから取得（重複排除）"""
    since = f"{date_str}T00:00:00Z"
    next_date = (
        datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    until = f"{next_date}T00:00:00Z"

    if not branches:
        branches = [None]

    seen_shas = set()
    commits = []

    for branch in branches:
        url = (
            f"/repos/{owner}/{repo}/commits"
            f"?author={username}&since={since}&until={until}&per_page=100"
        )
        if branch:
            url += f"&sha={branch}"

        data = run_gh_json(
            ["api", url, "--paginate"],
            allow_empty=True,
        )

        if not data or not isinstance(data, list):
            continue

        for c in data:
            full_sha = c["sha"]
            if full_sha in seen_shas:
                continue
            seen_shas.add(full_sha)
            message = c.get("commit", {}).get("message", "").split("\n")[0]
            commits.append({
                "sha": full_sha[:7],
                "full_sha": full_sha,
                "message": message,
                "url": c.get("html_url", ""),
                "date": c.get("commit", {}).get("author", {}).get("date", ""),
                "issues": extract_issue_numbers(
                    c.get("commit", {}).get("message", "")
                ),
            })
    return commits


def get_prs(owner, repo, username, date_str):
    """指定日に作成/更新されたPRを取得"""
    data = run_gh_json(
        [
            "pr", "list",
            "-R", f"{owner}/{repo}",
            "--author", username,
            "--state", "all",
            "--json", "number,title,url,state,mergedAt,createdAt,updatedAt,body,headRefName",
            "--limit", "100",
        ],
        allow_empty=True,
    )

    if not data or not isinstance(data, list):
        return []

    prs = []
    for pr in data:
        created = pr.get("createdAt", "")[:10]
        updated = pr.get("updatedAt", "")[:10]
        merged = pr.get("mergedAt", "")[:10] if pr.get("mergedAt") else ""

        if date_str in (created, updated, merged):
            body = pr.get("body", "") or ""
            title = pr.get("title", "") or ""
            issues = extract_issue_numbers(title + " " + body)
            prs.append({
                "number": pr["number"],
                "title": pr["title"],
                "url": pr["url"],
                "state": pr["state"],
                "merged": bool(pr.get("mergedAt")),
                "branch": pr.get("headRefName", ""),
                "issues": issues,
            })
    return prs


def get_pr_commit_shas(owner, repo, pr_number):
    """PRに含まれるコミットのSHAセットを取得"""
    url = f"/repos/{owner}/{repo}/pulls/{pr_number}/commits?per_page=100"
    data = run_gh_json(["api", url, "--paginate"], allow_empty=True)
    if not data or not isinstance(data, list):
        return set()
    return {c["sha"] for c in data}


def match_commits_to_prs(commits, prs, owner, repo):
    """コミットをPR単位でグルーピングする

    マッチ方法:
    1. PRのコミット一覧API（直接紐付き）
    2. "Merge pull request #NNN" パターン（マージコミット）
    """
    # PRごとのコミットSHAを取得
    pr_shas = {}
    for pr in prs:
        pr_shas[pr["number"]] = get_pr_commit_shas(owner, repo, pr["number"])

    # マージコミットからPR番号を検出
    merge_commit_to_pr = {}
    for c in commits:
        m = re.match(r"Merge pull request #(\d+)", c["message"])
        if m:
            merge_commit_to_pr[c["full_sha"]] = int(m.group(1))

    # グルーピング
    pr_commits = defaultdict(list)  # pr_number -> [commit, ...]
    orphan_commits = []
    matched_shas = set()

    for c in commits:
        full_sha = c["full_sha"]
        matched = False

        # 1. PRコミット一覧でマッチ
        for pr in prs:
            if full_sha in pr_shas.get(pr["number"], set()):
                pr_commits[pr["number"]].append(c)
                matched_shas.add(full_sha)
                matched = True
                break

        # 2. マージコミットとしてマッチ
        if not matched and full_sha in merge_commit_to_pr:
            pr_num = merge_commit_to_pr[full_sha]
            pr_commits[pr_num].append(c)
            matched_shas.add(full_sha)
            matched = True

        if not matched:
            orphan_commits.append(c)

    return dict(pr_commits), orphan_commits


def get_review_comments(owner, repo, username, date_str):
    """指定日のレビューコメントを取得"""
    since = f"{date_str}T00:00:00Z"
    url = (
        f"/repos/{owner}/{repo}/pulls/comments"
        f"?since={since}&per_page=100"
    )
    data = run_gh_json(
        ["api", url, "--paginate"],
        allow_empty=True,
    )

    if not data or not isinstance(data, list):
        return {}

    pr_comments = defaultdict(list)
    for comment in data:
        if comment.get("user", {}).get("login") != username:
            continue
        comment_date = comment.get("created_at", "")[:10]
        if comment_date != date_str:
            continue

        pr_url = comment.get("pull_request_url", "")
        pr_number_match = re.search(r"/pulls/(\d+)$", pr_url)
        if pr_number_match:
            pr_number = int(pr_number_match.group(1))
            pr_comments[pr_number].append({
                "body": comment.get("body", "")[:100],
                "url": comment.get("html_url", ""),
                "created_at": comment.get("created_at", ""),
            })

    return dict(pr_comments)


def get_issue_comments(owner, repo, username, date_str):
    """指定日のIssueコメント（PR会話タブ含む）を取得

    GitHub API の /issues/comments はIssueコメントとPR会話タブコメントの両方を返す。
    PR diff 上のインラインレビューコメント（/pulls/comments）は含まれない。

    Returns:
        tuple: (pr_conversation: dict[int, list], issue_comments: list)
            pr_conversation: PR番号 -> 会話コメントリスト
            issue_comments: 純粋なIssueへのコメントリスト
    """
    since = f"{date_str}T00:00:00Z"
    url = (
        f"/repos/{owner}/{repo}/issues/comments"
        f"?since={since}&per_page=100"
    )
    data = run_gh_json(
        ["api", url, "--paginate"],
        allow_empty=True,
    )

    if not data or not isinstance(data, list):
        return {}, []

    pr_conversation = defaultdict(list)
    issue_comments = []

    for comment in data:
        if comment.get("user", {}).get("login") != username:
            continue
        comment_date = comment.get("created_at", "")[:10]
        if comment_date != date_str:
            continue

        html_url = comment.get("html_url", "")
        issue_url = comment.get("issue_url", "")
        number_match = re.search(r"/issues/(\d+)$", issue_url)
        if not number_match:
            continue

        number = int(number_match.group(1))
        entry = {
            "body": (comment.get("body", "") or "")[:100],
            "url": html_url,
            "created_at": comment.get("created_at", ""),
        }

        # html_url に /pull/ が含まれればPR会話、そうでなければIssueコメント
        if "/pull/" in html_url:
            pr_conversation[number].append(entry)
        else:
            entry["issue_number"] = number
            issue_comments.append(entry)

    return dict(pr_conversation), issue_comments


def get_pr_reviews(owner, repo, username, date_str):
    """指定日のPRレビュー（approve/request changes等）を取得"""
    query = f"is:pr+reviewed-by:{username}+repo:{owner}/{repo}+updated:{date_str}..{date_str}"
    url = f"/search/issues?q={query}&per_page=100"
    data = run_gh_json(["api", url], allow_empty=True)

    if not data or not isinstance(data, dict):
        return []

    reviewed_prs = []
    for item in data.get("items", []):
        reviewed_prs.append({
            "number": item["number"],
            "title": item["title"],
            "url": item.get("html_url", ""),
        })
    return reviewed_prs


def get_merged_by_user(owner, repo, username, date_str):
    """指定日にユーザーがマージしたPR（自分が著者でないもの＝レビュー扱い）"""
    query = f"is:pr+is:merged+repo:{owner}/{repo}+merged:{date_str}..{date_str}"
    url = f"/search/issues?q={query}&per_page=100"
    data = run_gh_json(["api", url], allow_empty=True)

    if not data or not isinstance(data, dict):
        return []

    merged_prs = []
    for item in data.get("items", []):
        pr_detail = run_gh_json(
            ["api", f"/repos/{owner}/{repo}/pulls/{item['number']}"],
            allow_empty=True,
        )
        if not pr_detail:
            continue

        merged_by = pr_detail.get("merged_by", {}).get("login", "")
        author = pr_detail.get("user", {}).get("login", "")

        if merged_by == username and author != username:
            merged_prs.append({
                "number": item["number"],
                "title": item["title"],
                "url": item.get("html_url", ""),
                "author": author,
            })
    return merged_prs


def get_created_issues(owner, repo, username, date_str):
    """指定日にユーザーが作成したIssue（PRを除く）を取得"""
    since = f"{date_str}T00:00:00Z"
    next_date = (
        datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    until = f"{next_date}T00:00:00Z"

    url = (
        f"/repos/{owner}/{repo}/issues"
        f"?creator={username}&since={since}&state=all&per_page=100"
    )
    data = run_gh_json(
        ["api", url, "--paginate"],
        allow_empty=True,
    )
    if not data or not isinstance(data, list):
        return []

    created = []
    for item in data:
        # PRは除外
        if "pull_request" in item:
            continue
        created_at = item.get("created_at", "")
        if created_at >= since and created_at < until:
            created.append({
                "number": item["number"],
                "title": item.get("title", ""),
                "url": item.get("html_url", ""),
                "state": item.get("state", "open"),
                "labels": [l["name"] for l in item.get("labels", [])],
            })
    return created


def get_issue_details(owner, repo, issue_numbers):
    """Issue番号のリストからIssue詳細を取得"""
    issues = {}
    for num in issue_numbers:
        data = run_gh_json(
            ["api", f"/repos/{owner}/{repo}/issues/{num}"],
            allow_empty=True,
        )
        if data:
            issues[int(num)] = {
                "number": data["number"],
                "title": data.get("title", ""),
                "url": data.get("html_url", ""),
                "state": data.get("state", ""),
                "is_pr": "pull_request" in data,
            }
    return issues


# ============================================================
# Backlog連携
# ============================================================

def backlog_api(space, path, api_key, params=None):
    """Backlog REST APIを呼び出す"""
    url = f"https://{space}/api/v2{path}?apiKey={api_key}"
    if params:
        for k, v in params.items():
            if isinstance(v, list):
                for item in v:
                    url += f"&{k}[]={item}"
            else:
                url += f"&{k}={v}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"  Backlog API error: {e}", file=sys.stderr)
        return None


def get_backlog_user_id(space, api_key):
    """Backlog APIキーに紐づくユーザーIDと名前を取得"""
    # /users/myself は使えないため、/space/notification で自分のIDを取得する代わりに
    # APIキー認証でアクセスできるユーザー情報を取得
    data = backlog_api(space, "/users/myself", api_key)
    if data and isinstance(data, dict) and "id" in data:
        return data["id"], data.get("name", "")
    # /users/myself が使えない場合、スペース情報から取得を試みる
    # Backlog APIではapiKeyの所有者＝リクエスト者なので別経路で取得
    return None, None


def get_backlog_activities(space, api_key, user_id, date_str):
    """指定日のBacklog活動（コメント・Issue更新等）を取得"""
    # activityTypeId: 1=Issue作成, 2=Issue更新, 3=コメント追加
    data = backlog_api(space, f"/users/{user_id}/activities", api_key, {
        "count": "100",
        "activityTypeId": [1, 2, 3],
    })

    if not data or not isinstance(data, list):
        return []

    activities = []
    type_map = {1: "作成", 2: "更新", 3: "コメント"}

    for a in data:
        created = a.get("created", "")[:10]
        if created != date_str:
            continue

        content = a.get("content", {})
        project_key = a.get("project", {}).get("projectKey", "")
        issue_key_id = content.get("key_id", "")
        issue_key = f"{project_key}-{issue_key_id}" if project_key and issue_key_id else ""
        summary = content.get("summary", "")
        activity_type = type_map.get(a.get("type"), str(a.get("type")))

        comment = content.get("comment", {})
        comment_text = ""
        comment_id = ""
        if comment:
            comment_text = (comment.get("content", "") or "")
            comment_id = comment.get("id", "")

        # Issue URL
        issue_url = f"https://{space}/view/{issue_key}" if issue_key else ""
        comment_url = f"{issue_url}#comment-{comment_id}" if issue_url and comment_id else ""

        activities.append({
            "type": activity_type,
            "issue_key": issue_key,
            "summary": summary,
            "url": comment_url or issue_url,
            "comment_preview": comment_text[:120],
            "created": a.get("created", ""),
        })

    return activities


# ============================================================
# Project Status (GitHub Projects v2)
# ============================================================

def get_org_projects(org, include_closed=False):
    """org内のプロジェクト一覧を取得する"""
    query = """
query {
  organization(login: "%s") {
    projectsV2(first: 50) {
      nodes { id number title closed }
    }
  }
}
""" % org
    result = run_gh_json(["api", "graphql", "-f", f"query={query}"], allow_empty=True)
    if not result or "data" not in result:
        return []
    nodes = (result.get("data") or {}).get("organization", {}).get("projectsV2", {}).get("nodes") or []
    projects = []
    for n in nodes:
        if not include_closed and n.get("closed"):
            continue
        # untitled projectやtemplateはスキップ
        title = n.get("title", "")
        if "untitled" in title.lower() or "[TEMPLATE]" in title:
            continue
        projects.append({
            "id": n["id"],
            "number": n["number"],
            "title": title,
        })
    return projects


def get_all_project_items(org):
    """org内の全openプロジェクトからアイテムを一括取得する"""
    projects = get_org_projects(org)
    if not projects:
        return [], {}

    all_items = []
    project_map = {}  # number -> title
    for proj in projects:
        pnum = proj["number"]
        ptitle = proj["title"]
        project_map[pnum] = ptitle
        print(f"  📋 Project #{pnum}: {ptitle} ...", end="", flush=True)
        items, _ptitle = get_project_items(org, pnum)
        for it in items:
            it["project_name"] = _ptitle or ptitle
        print(f" {len(items)}件")
        all_items.extend(items)

    return all_items, project_map


def get_project_items(org, project_number):
    """Project のアイテムをステータス/ブロック情報付きで取得する (GraphQL)"""
    query = """
query($org: String!, $number: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $number) {
      title
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          content {
            ... on Issue {
              number
              title
              url
              state
              body
              assignees(first: 5) { nodes { login } }
              labels(first: 10) { nodes { name } }
              subIssues(first: 1) { totalCount }
              parent { number title url state repository { name nameWithOwner } }
              repository { name nameWithOwner }
            }
            ... on PullRequest {
              number
              title
              url
              state
              isDraft
              assignees(first: 5) { nodes { login } }
              repository { name nameWithOwner }
            }
          }
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          blocked: fieldValueByName(name: "Blocked") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          blockerType: fieldValueByName(name: "Blocker Type") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          startDate: fieldValueByName(name: "Start date") {
            ... on ProjectV2ItemFieldDateValue { date }
          }
        }
      }
    }
  }
}
"""
    all_items = []
    cursor = None
    project_title = ""

    while True:
        args = ["api", "graphql",
                "-f", f"query={query}",
                "-f", f"org={org}",
                "-F", f"number={project_number}"]
        if cursor:
            args.extend(["-f", f"cursor={cursor}"])

        result = run_gh_json(args, allow_empty=True)
        if not result or "data" not in result:
            break

        project = (result.get("data") or {}).get("organization", {}).get("projectV2") or {}
        if not project_title:
            project_title = project.get("title", "")
        items_data = project.get("items") or {}
        nodes = items_data.get("nodes") or []

        for node in nodes:
            content = node.get("content")
            if not content:
                continue

            is_pr = "isDraft" in content
            labels = [n["name"] for n in (content.get("labels") or {}).get("nodes") or []]
            sub_issue_count = (content.get("subIssues") or {}).get("totalCount", 0)
            # est:Xd パターンをIssue本文から抽出
            body = content.get("body") or ""
            est_match = re.search(r'est:(\d+(?:\.\d+)?d)', body)
            estimate = est_match.group(1) if est_match else ""
            start_date = (node.get("startDate") or {}).get("date", "")
            parent_data = content.get("parent")
            parent_info = None
            if parent_data:
                parent_info = {
                    "number": parent_data.get("number"),
                    "title": parent_data.get("title", ""),
                    "url": parent_data.get("url", ""),
                    "state": parent_data.get("state", ""),
                    "repo": (parent_data.get("repository") or {}).get("name", ""),
                    "repo_full": (parent_data.get("repository") or {}).get("nameWithOwner", ""),
                }
            item = {
                "number": content.get("number"),
                "title": content.get("title", ""),
                "url": content.get("url", ""),
                "state": content.get("state", ""),
                "is_draft": content.get("isDraft", False),
                "is_pr": is_pr,
                "is_parent": sub_issue_count > 0,
                "sub_issue_count": sub_issue_count,
                "parent": parent_info,
                "assignees": [n["login"] for n in (content.get("assignees") or {}).get("nodes") or []],
                "labels": labels,
                "repo": (content.get("repository") or {}).get("name", ""),
                "repo_full": (content.get("repository") or {}).get("nameWithOwner", ""),
                "status": (node.get("status") or {}).get("name", ""),
                "blocked": (node.get("blocked") or {}).get("name", ""),
                "blocker_type": (node.get("blockerType") or {}).get("name", ""),
                "start_date": start_date,
                "estimate": estimate,
                "project_number": project_number,
            }
            all_items.append(item)

        page_info = items_data.get("pageInfo") or {}
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
        else:
            break

    # In Progress / In Review アイテムの直近ステータス遷移日を一括取得
    status_target_issues = [
        it for it in all_items
        if not it["is_pr"] and it["status"] in ("In progress", "In review") and it.get("repo_full")
    ]
    if status_target_issues:
        since_map = fetch_status_since(status_target_issues)
        for it in all_items:
            key = (it.get("repo_full", ""), it.get("number"))
            it["in_progress_since"] = since_map.get((key[0], key[1], "In progress"), "")
            it["in_review_since"] = since_map.get((key[0], key[1], "In review"), "")

    # In Review アイテムの紐づくPRレビュー状態を取得
    in_review_issues = [
        it for it in all_items
        if not it["is_pr"] and it["status"] == "In review" and it.get("repo_full")
    ]
    if in_review_issues:
        review_map = fetch_review_state(in_review_issues)
        for it in all_items:
            key = (it.get("repo_full", ""), it.get("number"))
            it["review_state"] = review_map.get(key, "")

    return all_items, project_title


def fetch_status_since(issues):
    """アイテムのタイムラインから現在ステータスの開始日を一括取得する。

    各アイテムの現在のstatusに一致する直近のイベントのcreatedAtを返す。

    Returns:
        dict: (repo_full, number, status) -> ISO datetime string
    """
    result_map = {}

    batches = []
    current_batch = []
    for issue in issues:
        current_batch.append(issue)
        if len(current_batch) >= 10:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)

    for batch in batches:
        query_parts = []
        for i, issue in enumerate(batch):
            parts = issue["repo_full"].split("/", 1)
            if len(parts) != 2:
                continue
            owner, repo = parts
            num = issue["number"]
            query_parts.append(
                f'r{i}: repository(owner: "{owner}", name: "{repo}") {{'
                f'  issue(number: {num}) {{'
                f'    timelineItems(last: 20, itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) {{'
                f'      nodes {{'
                f'        ... on ProjectV2ItemStatusChangedEvent {{'
                f'          createdAt status'
                f'        }}'
                f'      }}'
                f'    }}'
                f'  }}'
                f'}}'
            )

        if not query_parts:
            continue

        query = "query {\n" + "\n".join(query_parts) + "\n}"
        resp = run_gh_json(["api", "graphql", "-f", f"query={query}"], allow_empty=True)
        if not resp or "data" not in resp:
            continue

        data = resp["data"] or {}
        for i, issue in enumerate(batch):
            alias = f"r{i}"
            repo_data = data.get(alias) or {}
            issue_data = repo_data.get("issue") or {}
            timeline = (issue_data.get("timelineItems") or {}).get("nodes") or []

            target_status = issue["status"]
            since = ""
            for event in reversed(timeline):
                if event.get("status") == target_status:
                    since = event.get("createdAt", "")
                    break

            if since:
                result_map[(issue["repo_full"], issue["number"], target_status)] = since

    return result_map


def fetch_review_state(issues):
    """In ReviewアイテムのIssueに紐づくオープンPRのレビュー状態を一括取得する。

    IssueのtimelineからCrossReferencedEventでPRを辿り、
    そのPRのreviewDecisionを取得する。

    Returns:
        dict: (repo_full, number) -> review state string
              "APPROVED" | "CHANGES_REQUESTED" | "REVIEW_REQUIRED" | ""
    """
    result_map = {}

    batches = []
    current_batch = []
    for issue in issues:
        current_batch.append(issue)
        if len(current_batch) >= 5:
            batches.append(current_batch)
            current_batch = []
    if current_batch:
        batches.append(current_batch)

    for batch in batches:
        query_parts = []
        for i, issue in enumerate(batch):
            parts = issue["repo_full"].split("/", 1)
            if len(parts) != 2:
                continue
            owner, repo = parts
            num = issue["number"]
            query_parts.append(
                f'r{i}: repository(owner: "{owner}", name: "{repo}") {{'
                f'  issue(number: {num}) {{'
                f'    timelineItems(last: 30, itemTypes: [CROSS_REFERENCED_EVENT]) {{'
                f'      nodes {{'
                f'        ... on CrossReferencedEvent {{'
                f'          source {{'
                f'            ... on PullRequest {{'
                f'              number state reviewDecision'
                f'              closingIssuesReferences(first: 10) {{ nodes {{ number }} }}'
                f'            }}'
                f'          }}'
                f'        }}'
                f'      }}'
                f'    }}'
                f'  }}'
                f'}}'
            )

        if not query_parts:
            continue

        query = "query {\n" + "\n".join(query_parts) + "\n}"
        resp = run_gh_json(["api", "graphql", "-f", f"query={query}"], allow_empty=True)
        if not resp or "data" not in resp:
            continue

        data = resp["data"] or {}
        for i, issue in enumerate(batch):
            alias = f"r{i}"
            repo_data = data.get(alias) or {}
            issue_data = repo_data.get("issue") or {}
            timeline = (issue_data.get("timelineItems") or {}).get("nodes") or []

            # オープンPRのうち、このIssueをclosingIssuesReferencesに含むものを探す
            review_decision = ""
            for event in reversed(timeline):
                source = event.get("source") or {}
                if not source.get("number"):
                    continue  # PR以外のsource
                if source.get("state") != "OPEN":
                    continue
                # closingIssuesReferencesにこのIssueが含まれるか確認
                closing_nums = [
                    n.get("number") for n in
                    (source.get("closingIssuesReferences") or {}).get("nodes") or []
                ]
                if issue["number"] in closing_nums:
                    review_decision = source.get("reviewDecision") or ""
                    break
                # closingIssuesReferencesが空でもオープンPRなら候補とする（フォールバック）
                if not review_decision and not closing_nums:
                    review_decision = source.get("reviewDecision") or ""

            if review_decision:
                result_map[(issue["repo_full"], issue["number"])] = review_decision

    return result_map


def fetch_sub_issues(repo_full, issue_number):
    """親Issueの子Issue一覧を取得する (GraphQL)"""
    query = """
query($owner: String!, $repo: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      subIssues(first: 50, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          number
          title
          url
          state
          repository { name nameWithOwner }
        }
      }
    }
  }
}
"""
    parts = repo_full.split("/", 1)
    if len(parts) != 2:
        return []
    owner, repo = parts

    children = []
    cursor = None
    while True:
        args = ["api", "graphql",
                "-f", f"query={query}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"number={issue_number}"]
        if cursor:
            args.extend(["-f", f"cursor={cursor}"])

        result = run_gh_json(args, allow_empty=True)
        if not result or "data" not in result:
            break

        issue_data = ((result.get("data") or {}).get("repository") or {}).get("issue") or {}
        sub_data = issue_data.get("subIssues") or {}
        for node in sub_data.get("nodes") or []:
            children.append({
                "number": node.get("number"),
                "title": node.get("title", ""),
                "url": node.get("url", ""),
                "state": node.get("state", ""),
                "repo": (node.get("repository") or {}).get("name", ""),
                "repo_full": (node.get("repository") or {}).get("nameWithOwner", ""),
            })

        page_info = sub_data.get("pageInfo") or {}
        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
        else:
            break

    return children


def build_tree_for_next_actions(next_actions, project_items):
    """next_actionsリストを親子ツリー構造に変換する。

    子がIn ProgressならステータスにかかわらずProjectから親を取得し、
    その親の全子Issueをツリーとして表示する。

    Returns:
        list of tree entries:
        - 親子関係のないアイテム: {"type": "flat", "action": na}
        - 親課題ツリー: {"type": "tree", "parent": {...}, "children": [...],
                         "action": na (親自身のaction, or None)}
    """
    # project_itemsからnumber→itemのマップを作成
    item_by_number = {}
    for it in project_items:
        if not it["is_pr"]:
            item_by_number[it["number"]] = it

    # In Progressの子がいる親を収集
    parent_numbers_needed = set()  # (repo_full, parent_number)
    child_actions_by_parent = {}   # parent_number -> [na, ...]
    flat_actions = []
    parent_self_actions = {}       # parent_number -> na (親自身がnext_actionsにいる場合)

    # 1st pass: 子がIn Progressな親を収集
    for na in next_actions:
        item = na["item"]
        parent_info = item.get("parent")
        if parent_info and item.get("status") == "In progress":
            p_num = parent_info["number"]
            p_repo_full = parent_info.get("repo_full", "")
            parent_numbers_needed.add((p_repo_full, p_num))
            child_actions_by_parent.setdefault(p_num, []).append(na)

    # 2nd pass: 親自身がnext_actionsにいるケースを回収
    for na in next_actions:
        item = na["item"]
        if item.get("is_parent") and item["number"] in child_actions_by_parent:
            parent_self_actions[item["number"]] = na

    # 親ごとに子Issueをfetchしてツリーを構築
    trees = []
    seen_in_tree = set()  # ツリーに含まれたaction item numberを記録

    for (repo_full, parent_num) in sorted(parent_numbers_needed):
        # 親の情報: project_itemsにあればそちら、なければparent_infoから構築
        parent_item = item_by_number.get(parent_num)
        if not parent_item:
            # Projectにない親 — 子のparent_infoから構築
            sample_child = child_actions_by_parent[parent_num][0]["item"]
            pi = sample_child["parent"]
            parent_item = {
                "number": pi["number"],
                "title": pi["title"],
                "url": pi["url"],
                "state": pi["state"],
                "repo": pi.get("repo", ""),
                "repo_full": pi.get("repo_full", ""),
                "status": "",  # Projectに無いのでステータス不明
            }

        # 子Issue一覧を取得
        children = fetch_sub_issues(repo_full, parent_num)

        # 子にProjectステータスを付与
        for child in children:
            proj_item = item_by_number.get(child["number"])
            if proj_item:
                child["status"] = proj_item.get("status", "")
                child["blocked"] = proj_item.get("blocked", "")
                child["start_date"] = proj_item.get("start_date", "")
                child["estimate"] = proj_item.get("estimate", "")
                child["in_progress_since"] = proj_item.get("in_progress_since", "")
                child["in_review_since"] = proj_item.get("in_review_since", "")
                child["review_state"] = proj_item.get("review_state", "")
            else:
                child["status"] = ""
                child["blocked"] = ""
                child["start_date"] = ""
                child["estimate"] = ""
                child["in_progress_since"] = ""
                child["in_review_since"] = ""
                child["review_state"] = ""

        # ツリーに含まれる子のnumberを記録
        for ch_na in child_actions_by_parent.get(parent_num, []):
            seen_in_tree.add(ch_na["item"]["number"])

        parent_action = parent_self_actions.get(parent_num)
        if parent_action:
            seen_in_tree.add(parent_num)

        trees.append({
            "type": "tree",
            "parent": parent_item,
            "children": children,
            "action": parent_action,
        })

    # flat actionsから、ツリーに含まれたものを除外
    result = []
    for na in next_actions:
        item = na["item"]
        if item["number"] in seen_in_tree:
            continue
        result.append({"type": "flat", "action": na})

    # ツリーを挿入（最初のflat継続作業の前、またはブロック中の後）
    # ブロック中の後に挿入する
    insert_pos = 0
    for i, entry in enumerate(result):
        if entry["type"] == "flat" and entry["action"]["category"] != "ブロック中":
            insert_pos = i
            break
    else:
        insert_pos = len(result)

    for tree in reversed(trees):
        result.insert(insert_pos, tree)

    return result


def fetch_review_requested_prs(org, username):
    """ユーザーにレビュー依頼されているオープンPRをorg全体から取得する。

    Returns:
        list of dict: PR情報 (number, title, url, repo, repo_full,
                       review_decision, linked_issue_numbers)
    """
    query = f"org:{org} is:pr is:open review-requested:{username}"
    result = run_gh_json(
        ["api", "graphql", "-f", f"""query=query {{
  search(query: "{query}", type: ISSUE, first: 30) {{
    nodes {{
      ... on PullRequest {{
        number
        title
        url
        state
        isDraft
        reviewDecision
        repository {{ name nameWithOwner }}
        closingIssuesReferences(first: 10) {{ nodes {{ number }} }}
      }}
    }}
  }}
}}"""],
        allow_empty=True,
    )
    if not result or "data" not in result:
        return []

    prs = []
    nodes = ((result.get("data") or {}).get("search") or {}).get("nodes") or []
    for node in nodes:
        if not node.get("number"):
            continue
        closing_nums = [
            n.get("number") for n in
            (node.get("closingIssuesReferences") or {}).get("nodes") or []
        ]
        prs.append({
            "number": node.get("number"),
            "title": node.get("title", ""),
            "url": node.get("url", ""),
            "is_draft": node.get("isDraft", False),
            "review_decision": node.get("reviewDecision") or "",
            "repo": (node.get("repository") or {}).get("name", ""),
            "repo_full": (node.get("repository") or {}).get("nameWithOwner", ""),
            "linked_issue_numbers": closing_nums,
            "in_review_since": "",
        })

    # PRのIn Review開始日をタイムラインから一括取得
    if prs:
        batches = []
        current_batch = []
        for pr in prs:
            current_batch.append(pr)
            if len(current_batch) >= 10:
                batches.append(current_batch)
                current_batch = []
        if current_batch:
            batches.append(current_batch)

        for batch in batches:
            query_parts = []
            for i, pr in enumerate(batch):
                parts = pr["repo_full"].split("/", 1)
                if len(parts) != 2:
                    continue
                owner, repo = parts
                query_parts.append(
                    f'r{i}: repository(owner: "{owner}", name: "{repo}") {{'
                    f'  pullRequest(number: {pr["number"]}) {{'
                    f'    timelineItems(last: 20, itemTypes: [PROJECT_V2_ITEM_STATUS_CHANGED_EVENT]) {{'
                    f'      nodes {{'
                    f'        ... on ProjectV2ItemStatusChangedEvent {{'
                    f'          createdAt status'
                    f'        }}'
                    f'      }}'
                    f'    }}'
                    f'  }}'
                    f'}}'
                )
            if not query_parts:
                continue
            q = "query {\n" + "\n".join(query_parts) + "\n}"
            resp = run_gh_json(["api", "graphql", "-f", f"query={q}"], allow_empty=True)
            if not resp or "data" not in resp:
                continue
            data = resp["data"] or {}
            for i, pr in enumerate(batch):
                repo_data = data.get(f"r{i}") or {}
                pr_data = repo_data.get("pullRequest") or {}
                timeline = (pr_data.get("timelineItems") or {}).get("nodes") or []
                for event in reversed(timeline):
                    if event.get("status") == "In review":
                        pr["in_review_since"] = event.get("createdAt", "")
                        break

    return prs


def build_project_summary(project_items, username, today_active_issues,
                          review_requested_prs=None):
    """プロジェクトアイテムからステータスサマリーを構築する

    Args:
        project_items: get_project_items() の戻り値
        username: GitHubユーザー名
        today_active_issues: 本日活動のあったIssue番号のセット (int)
        review_requested_prs: fetch_review_requested_prs() の戻り値 (None可)

    Returns:
        dict with keys: status_counts, in_progress, in_review, blocked,
        today_status_context, next_actions
    """
    status_counts = defaultdict(int)
    in_progress = []
    in_review = []
    blocked = []
    today_status_context = []

    for item in project_items:
        if item["is_pr"]:
            continue  # PRはスキップ、Issueのみ対象

        status = item["status"]
        if status:
            status_counts[status] += 1

        is_mine = username in item["assignees"]
        is_today_active = item["number"] in today_active_issues

        if is_mine or is_today_active:
            enriched = {**item, "today_active": is_today_active, "is_mine": is_mine}

            if status == "In progress":
                in_progress.append(enriched)
            elif status == "In review":
                in_review.append(enriched)

            if item["blocked"] == "Yes":
                blocked.append(enriched)

            if is_today_active:
                today_status_context.append(enriched)

    # ステータス別タスク一覧: 全アイテムをカテゴリ分けして統合表示
    next_actions = []
    seen_numbers = set()

    # 1. ブロック中 (全ステータス, Blocked=Yes) — 最優先で表示
    for item in blocked:
        if item["number"] not in seen_numbers:
            next_actions.append({
                "category": "ブロック中",
                "item": item,
                "reason": f'{item["status"] or "未設定"} / ブロック: {item["blocker_type"]}',
            })
            seen_numbers.add(item["number"])

    # 2. In Progress + 本日活動あり → 継続作業
    for item in in_progress:
        if item["today_active"] and item["number"] not in seen_numbers:
            next_actions.append({
                "category": "継続作業",
                "item": item,
                "reason": "In Progress / 本日作業あり",
            })
            seen_numbers.add(item["number"])

    # 3. In Progress + 本日活動なし → 本日未更新
    for item in in_progress:
        if not item["today_active"] and item["number"] not in seen_numbers:
            next_actions.append({
                "category": "本日未更新",
                "item": item,
                "reason": "In Progress / 本日活動なし",
            })
            seen_numbers.add(item["number"])

    # 4. In Review → レビュー待ち
    for item in in_review:
        if item["number"] not in seen_numbers:
            next_actions.append({
                "category": "レビュー待ち",
                "item": item,
                "reason": "In Review",
            })
            seen_numbers.add(item["number"])

    # 5. レビュー依頼されているPR → レビュー依頼
    seen_pr_numbers = set()
    if review_requested_prs:
        # Project内Issueのnumber→itemマップ
        item_by_number = {}
        for it in project_items:
            if not it["is_pr"]:
                item_by_number[it["number"]] = it

        for pr in review_requested_prs:
            # PRに紐づくIssueがProjectにあればそのIssueで表示
            linked_issue = None
            for inum in pr.get("linked_issue_numbers", []):
                if inum in item_by_number and inum not in seen_numbers:
                    linked_issue = item_by_number[inum]
                    break

            if linked_issue and linked_issue["number"] not in seen_numbers:
                enriched = {
                    **linked_issue,
                    "today_active": linked_issue["number"] in today_active_issues,
                    "is_mine": username in linked_issue.get("assignees", []),
                    "review_pr": pr,
                }
                # Issue自身のin_review_sinceが空ならPRのものを使う
                if not enriched.get("in_review_since") and pr.get("in_review_since"):
                    enriched["in_review_since"] = pr["in_review_since"]
                next_actions.append({
                    "category": "レビュー依頼",
                    "item": enriched,
                    "reason": f'PR #{pr["number"]} のレビュー依頼',
                })
                seen_numbers.add(linked_issue["number"])
            elif pr["number"] not in seen_pr_numbers:
                # Projectに紐づくIssueがない場合はPR自体を表示
                pr_item = {
                    "number": pr["number"],
                    "title": pr["title"],
                    "url": pr["url"],
                    "state": "OPEN",
                    "is_pr": True,
                    "is_draft": pr.get("is_draft", False),
                    "is_parent": False,
                    "sub_issue_count": 0,
                    "parent": None,
                    "assignees": [],
                    "labels": [],
                    "repo": pr.get("repo", ""),
                    "repo_full": pr.get("repo_full", ""),
                    "status": "In review",
                    "blocked": "",
                    "blocker_type": "",
                    "start_date": "",
                    "estimate": "",
                    "in_progress_since": "",
                    "in_review_since": pr.get("in_review_since", ""),
                    "review_state": pr.get("review_decision", ""),
                    "review_pr": pr,
                }
                next_actions.append({
                    "category": "レビュー依頼",
                    "item": pr_item,
                    "reason": f'PR #{pr["number"]} のレビュー依頼',
                })
                seen_pr_numbers.add(pr["number"])

    # 6. 本日活動あり + 上記以外のステータス → 本日活動
    for item in today_status_context:
        if item["number"] not in seen_numbers:
            next_actions.append({
                "category": "本日活動",
                "item": item,
                "reason": f'{item["status"] or "未設定"} / 本日活動あり',
            })
            seen_numbers.add(item["number"])

    return {
        "status_counts": dict(status_counts),
        "in_progress": in_progress,
        "in_review": in_review,
        "blocked": blocked,
        "today_status_context": today_status_context,
        "next_actions": next_actions,
    }


# ============================================================
# 明日の予定推測
# ============================================================

def get_tomorrow_plan(owner, repo, username, date_str, prs, review_comments):
    """今日の活動＋オープン状態のPR/Issueから明日の予定を推測する"""
    plan_items = []

    # 1. 今日触ったオープンPR → 「継続作業」
    today_open_prs = [pr for pr in prs if pr["state"] == "OPEN"]
    for pr in today_open_prs:
        plan_items.append({
            "category": "継続",
            "text": f'#{pr["number"]} {pr["title"]}',
            "url": pr["url"],
            "reason": "本日作業中のPR",
            "repo": repo,
        })

    # 2. レビュー依頼が来ているPR → 「レビュー予定」
    review_requested = run_gh_json(
        ["pr", "list", "-R", f"{owner}/{repo}",
         "--search", f"review-requested:{username}",
         "--state", "open",
         "--json", "number,title,url",
         "--limit", "20"],
        allow_empty=True,
    )
    if review_requested and isinstance(review_requested, list):
        for pr in review_requested:
            plan_items.append({
                "category": "レビュー",
                "text": f'#{pr["number"]} {pr["title"]}',
                "url": pr["url"],
                "reason": "レビュー依頼あり",
                "repo": repo,
            })

    # 3. 今日レビューコメントしたPRでまだOpenのもの → 「レビュー継続」
    for pr_number in review_comments.keys():
        # 既にレビュー依頼リストにある場合はスキップ
        already_listed = any(
            f"#{pr_number} " in item["text"] for item in plan_items
        )
        if already_listed:
            continue
        pr_data = run_gh_json(
            ["api", f"/repos/{owner}/{repo}/pulls/{pr_number}"],
            allow_empty=True,
        )
        if pr_data and pr_data.get("state") == "open":
            plan_items.append({
                "category": "レビュー",
                "text": f'#{pr_number} {pr_data.get("title", "")}',
                "url": pr_data.get("html_url", ""),
                "reason": "本日レビュー中",
                "repo": repo,
            })

    # 4. 自分にアサインされたオープンIssueのうち、今日のPR/コミットに関連するもの
    assigned_issues = run_gh_json(
        ["issue", "list", "-R", f"{owner}/{repo}",
         "--assignee", username, "--state", "open",
         "--json", "number,title,url",
         "--limit", "50"],
        allow_empty=True,
    )
    # 今日のPRに紐づくIssue番号を収集
    today_issue_nums = set()
    for pr in prs:
        today_issue_nums.update(int(n) for n in pr.get("issues", []))

    if assigned_issues and isinstance(assigned_issues, list):
        for issue in assigned_issues:
            # 既にPR継続で出ているものはスキップ
            already_listed = any(
                f"#{issue['number']} " in item["text"] for item in plan_items
            )
            if already_listed:
                continue
            # 今日のPRに関連するIssueのみ追加
            if issue["number"] in today_issue_nums:
                plan_items.append({
                    "category": "対応予定",
                    "text": f'#{issue["number"]} {issue["title"]}',
                    "url": issue["url"],
                    "reason": "本日の作業に関連",
                    "repo": repo,
                })

    return plan_items


# ============================================================
# HTML生成
# ============================================================

def generate_html(
    date_str, org, username,
    per_repo_data,
    backlog_activities=None,
    tomorrow_plan=None,
    single_repo=False,
    project_summary=None,
    project_items=None,
):
    """HTML日報を生成する（マルチリポ対応、ブラウザ表示用 + Slackコピー用）

    Args:
        per_repo_data: {repo_name: {owner, repo, prs, pr_commits, orphan_commits,
                        review_comments, reviewed_prs, merged_prs, issues,
                        issue_comments, created_issues}}
        single_repo: Trueなら従来の単一リポ表示（アコーディオンなし）
        project_summary: build_project_summary() の戻り値 (None可)
    """

    # --- 共通ヘルパー ---

    def _issue_link_parts(issue_nums, issues):
        """Issue番号リストから (実Issue のみ) リンク部品を返す"""
        links = []
        for num in issue_nums:
            num = int(num)
            if num in issues and not issues[num].get("is_pr"):
                info = issues[num]
                links.append((num, info))
            elif num not in issues:
                links.append((num, None))
        return links

    def issue_links_html(issue_nums, repo_url, issues):
        """ブラウザ用: PR配下の関連Issue（コンパクト表示）"""
        parts = _issue_link_parts(issue_nums, issues)
        if not parts:
            return ""
        links = []
        for num, info in parts:
            if info:
                links.append(f'<a href="{escape(info["url"])}">#{num} {escape(info["title"])}</a>')
            else:
                links.append(f'<a href="{repo_url}/issues/{num}">#{num}</a>')
        if len(links) == 1:
            return '<div class="related-issues">→ ' + links[0] + "</div>"
        return '<div class="related-issues">' + "".join(f'<div>→ {l}</div>' for l in links) + "</div>"

    def issue_inline_html(issue_nums, repo_url, issues):
        """ブラウザ用: コミット行末のインラインIssueリンク"""
        parts = _issue_link_parts(issue_nums, issues)
        if not parts:
            return ""
        links = []
        for num, info in parts:
            if info:
                links.append(f'<a href="{escape(info["url"])}">#{num}</a>')
            else:
                links.append(f'<a href="{repo_url}/issues/{num}">#{num}</a>')
        return ' <span class="inline-issue">→ ' + ", ".join(links) + "</span>"

    def slack_issue_links(issue_nums, repo_url, issues):
        """Slack用: 関連Issueリンク"""
        parts = _issue_link_parts(issue_nums, issues)
        if not parts:
            return ""
        links = []
        for num, info in parts:
            if info:
                links.append(f'<a href="{escape(info["url"])}">#{num} {escape(info["title"])}</a>')
            else:
                links.append(f'<a href="{repo_url}/issues/{num}">#{num}</a>')
        return "".join(f"<br>　→ {l}" for l in links)

    def slack_issue_inline(issue_nums, repo_url, issues):
        """Slack用: コミット行末のインラインIssueリンク"""
        parts = _issue_link_parts(issue_nums, issues)
        if not parts:
            return ""
        links = []
        for num, info in parts:
            if info:
                links.append(f'<a href="{escape(info["url"])}">#{num}</a>')
            else:
                links.append(f'<a href="{repo_url}/issues/{num}">#{num}</a>')
        return " → " + ", ".join(links)

    def state_badge(state, merged=False):
        if merged:
            return '<span class="badge badge-merged">Merged</span>'
        elif state == "OPEN":
            return '<span class="badge badge-open">Open</span>'
        elif state == "CLOSED":
            return '<span class="badge badge-closed">Closed</span>'
        return f'<span class="badge">{escape(state)}</span>'

    def slack_state_label(state, merged=False):
        if merged:
            return "[Merged]"
        elif state == "OPEN":
            return "[Open]"
        elif state == "CLOSED":
            return "[Closed]"
        return f"[{escape(state)}]"

    def commit_li_html(c, repo_url, issues, show_issues=False):
        """ブラウザ用コミット行"""
        issue_html = issue_inline_html(c["issues"], repo_url, issues) if show_issues else ""
        return (
            f'<li class="commit-item">'
            f'<a href="{escape(c["url"])}" class="sha">{escape(c["sha"])}</a> '
            f'{escape(c["message"])}{issue_html}</li>'
        )

    def slack_commit_li(c, repo_url, issues, show_issues=False):
        """Slack用コミット行"""
        issue_html = slack_issue_inline(c["issues"], repo_url, issues) if show_issues else ""
        return (
            f'<li><a href="{escape(c["url"])}">{escape(c["sha"])}</a> '
            f'{escape(c["message"])}{issue_html}</li>'
        )

    # --- Per-repo content generation ---

    def build_repo_browser_content(rd, show_empty_sections=False):
        """1リポ分のブラウザ表示用HTML部品を生成

        Args:
            rd: リポデータ dict
            show_empty_sections: Trueなら活動なしのセクションも表示

        Returns:
            (content_html, stats_dict)
        """
        owner = rd["owner"]
        repo = rd["repo"]
        repo_url = f"https://github.com/{owner}/{repo}"
        prs = rd["prs"]
        pr_commits = rd["pr_commits"]
        orphan_commits = rd["orphan_commits"]
        review_comments = rd["review_comments"]
        reviewed_prs = rd["reviewed_prs"]
        merged_prs = rd["merged_prs"]
        issues = rd["issues"]
        issue_comments_data = rd.get("issue_comments", [])
        created_issues_data = rd.get("created_issues", [])

        # h_tag: single_repo=h2, multi_repo=h3
        h_tag = "h2" if show_empty_sections else "h3"

        total_commits = sum(len(cs) for cs in pr_commits.values()) + len(orphan_commits)

        # PR & コミット
        pr_section_parts = []
        for pr in prs:
            commits_for_pr = pr_commits.get(pr["number"], [])
            badge = state_badge(pr["state"], pr["merged"])
            issue_html = issue_links_html(pr["issues"], repo_url, issues)
            commit_items = "".join(commit_li_html(c, repo_url, issues) for c in commits_for_pr)
            commit_list = f'<ul class="pr-commit-list">{commit_items}</ul>' if commits_for_pr else ""
            pr_section_parts.append(
                f'<div class="pr-group">'
                f'<div class="pr-header">{badge} '
                f'<a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a>'
                f'{issue_html}</div>'
                f'{commit_list}'
                f'</div>'
            )

        if orphan_commits:
            commit_items = "".join(
                commit_li_html(c, repo_url, issues, show_issues=True) for c in orphan_commits
            )
            pr_section_parts.append(
                f'<div class="pr-group orphan">'
                f'<div class="pr-header orphan-header">PR不在のコミット ({len(orphan_commits)})</div>'
                f'<ul class="pr-commit-list">{commit_items}</ul>'
                f'</div>'
            )

        if pr_section_parts:
            main_section_html = (
                f'<{h_tag}>🔀 PR & コミット ({len(prs)} PR / {total_commits} commits)</{h_tag}>'
                + "".join(pr_section_parts)
            )
        elif show_empty_sections:
            main_section_html = (
                f'<{h_tag}>🔀 PR & コミット</{h_tag}>'
                '<p class="empty">活動なし</p>'
            )
        else:
            main_section_html = ""

        # レビュー
        review_items = []
        comment_pr_numbers = set(review_comments.keys())
        for pr_number, comments_list in review_comments.items():
            pr_url = f"{repo_url}/pull/{pr_number}"
            review_items.append(
                f'<li class="item">'
                f'💬 <a href="{escape(pr_url)}">#{pr_number}</a> にレビューコメント {len(comments_list)}件</li>'
            )
        for pr in reviewed_prs:
            if pr["number"] not in comment_pr_numbers:
                review_items.append(
                    f'<li class="item">'
                    f'👀 <a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a> をレビュー</li>'
                )
        for pr in merged_prs:
            review_items.append(
                f'<li class="item">'
                f'✅ <a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a> '
                f'をマージ (by @{escape(pr["author"])})</li>'
            )
        if review_items:
            reviews_html = (
                f'<{h_tag}>📝 レビュー ({len(review_items)})</{h_tag}>'
                f'<ul>{"".join(review_items)}</ul>'
            )
        elif show_empty_sections:
            reviews_html = (
                f'<{h_tag}>📝 レビュー (0)</{h_tag}>'
                '<p class="empty">レビューなし</p>'
            )
        else:
            reviews_html = ""

        # Issueコメントセクション
        issue_comments_html = ""
        if issue_comments_data:
            ic_items = []
            for ic in issue_comments_data:
                num = ic["issue_number"]
                info = issues.get(num)
                title = escape(info["title"]) if info else ""
                issue_link = escape(info["url"]) if info else f"{repo_url}/issues/{num}"
                preview = escape((ic["body"].split("\n")[0])[:80])
                if preview:
                    preview = f' <span class="backlog-preview">— {preview}</span>'
                ic_items.append(
                    f'<li class="item">'
                    f'💬 <a href="{issue_link}">#{num} {title}</a>'
                    f'{preview}</li>'
                )
            issue_comments_html = (
                f'<{h_tag}>📌 Issue コメント ({len(issue_comments_data)})</{h_tag}>'
                f'<ul>{"".join(ic_items)}</ul>'
            )

        # 作成Issueセクション
        created_issues_html = ""
        if created_issues_data:
            ci_items = []
            for ci in created_issues_data:
                labels_html = ""
                if ci.get("labels"):
                    labels_html = " " + " ".join(
                        f'<span class="pr-label">{escape(l)}</span>' for l in ci["labels"]
                    )
                ci_items.append(
                    f'<li class="item">'
                    f'<a href="{escape(ci["url"])}">#{ci["number"]}</a> {escape(ci["title"])}'
                    f'{labels_html}</li>'
                )
            created_issues_html = (
                f'<{h_tag}>🎫 作成した Issue ({len(created_issues_data)})</{h_tag}>'
                f'<ul>{"".join(ci_items)}</ul>'
            )

        content_html = main_section_html + reviews_html + issue_comments_html + created_issues_html
        stats = {
            "prs": len(prs),
            "commits": total_commits,
            "reviews": len(review_items),
        }
        return content_html, stats

    def build_repo_slack_content(rd):
        """1リポ分のSlack用HTML部品を生成"""
        owner = rd["owner"]
        repo = rd["repo"]
        repo_url = f"https://github.com/{owner}/{repo}"
        prs = rd["prs"]
        pr_commits = rd["pr_commits"]
        orphan_commits = rd["orphan_commits"]
        review_comments = rd["review_comments"]
        reviewed_prs = rd["reviewed_prs"]
        merged_prs = rd["merged_prs"]
        issues = rd["issues"]
        issue_comments_data = rd.get("issue_comments", [])
        created_issues_data = rd.get("created_issues", [])

        total_commits = sum(len(cs) for cs in pr_commits.values()) + len(orphan_commits)

        slack_parts = []

        # PR & コミット
        slack_pr_parts = []
        for pr in prs:
            commits_for_pr = pr_commits.get(pr["number"], [])
            label = slack_state_label(pr["state"], pr["merged"])
            issue_link = slack_issue_links(pr["issues"], repo_url, issues)
            commit_items = "".join(slack_commit_li(c, repo_url, issues) for c in commits_for_pr)
            commit_list = f'<ul>{commit_items}</ul>' if commits_for_pr else ""
            slack_pr_parts.append(
                f'<strong>{label} <a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a></strong>'
                f'{issue_link}{commit_list}'
            )
        if orphan_commits:
            commit_items = "".join(
                slack_commit_li(c, repo_url, issues, show_issues=True) for c in orphan_commits
            )
            slack_pr_parts.append(
                f'<strong>PR不在のコミット ({len(orphan_commits)})</strong>'
                f'<ul>{commit_items}</ul>'
            )
        if slack_pr_parts:
            slack_parts.append(
                f'<strong>🔀 PR & コミット ({len(prs)} PR / {total_commits} commits)</strong><br>'
                + "<br>".join(slack_pr_parts)
            )

        # レビュー
        comment_pr_numbers = set(review_comments.keys())
        slack_review_items = []
        for pr_number, comments_list in review_comments.items():
            pr_url = f"{repo_url}/pull/{pr_number}"
            slack_review_items.append(
                f'<li>💬 <a href="{escape(pr_url)}">#{pr_number}</a> にレビューコメント {len(comments_list)}件</li>'
            )
        for pr in reviewed_prs:
            if pr["number"] not in comment_pr_numbers:
                slack_review_items.append(
                    f'<li>👀 <a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a> をレビュー</li>'
                )
        for pr in merged_prs:
            slack_review_items.append(
                f'<li>✅ <a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a> '
                f'をマージ (by @{escape(pr["author"])})</li>'
            )
        if slack_review_items:
            slack_parts.append(
                f'<strong>📝 レビュー ({len(slack_review_items)})</strong>'
                f'<ul>{"".join(slack_review_items)}</ul>'
            )

        # Issueコメント（Slack用）
        if issue_comments_data:
            items = []
            for ic in issue_comments_data:
                num = ic["issue_number"]
                info = issues.get(num)
                title = escape(info["title"]) if info else ""
                issue_link = escape(info["url"]) if info else f"{repo_url}/issues/{num}"
                preview = escape((ic["body"].split("\n")[0])[:60])
                preview_html = f" — {preview}" if preview else ""
                items.append(
                    f'<li>💬 <a href="{issue_link}">#{num} {title}</a>{preview_html}</li>'
                )
            slack_parts.append(
                f'<strong>📌 Issue コメント ({len(issue_comments_data)})</strong>'
                f'<ul>{"".join(items)}</ul>'
            )

        # 作成Issue（Slack用）
        if created_issues_data:
            items = []
            for ci in created_issues_data:
                items.append(
                    f'<li><a href="{escape(ci["url"])}">#{ci["number"]}</a> {escape(ci["title"])}</li>'
                )
            slack_parts.append(
                f'<strong>🎫 作成した Issue ({len(created_issues_data)})</strong>'
                f'<ul>{"".join(items)}</ul>'
            )

        return slack_parts

    # === Build per-repo sections ===
    browser_repo_sections = []
    slack_sections = []

    # Slack header
    if single_repo:
        rd = list(per_repo_data.values())[0]
        repo_url = f"https://github.com/{rd['owner']}/{rd['repo']}"
        slack_sections.append(
            f'<strong>日報 {date_str}</strong><br>'
            f'<a href="{repo_url}">{rd["owner"]}/{rd["repo"]}</a> | @{username}'
        )
    else:
        slack_sections.append(
            f'<strong>日報 {date_str}</strong><br>'
            f'{escape(org)} | @{username}'
        )

    for repo_key in sorted(per_repo_data.keys()):
        rd = per_repo_data[repo_key]

        browser_html, stats = build_repo_browser_content(
            rd, show_empty_sections=single_repo,
        )
        slack_parts = build_repo_slack_content(rd)

        if not browser_html and not single_repo:
            continue  # Multi-repo: skip repos with no content

        if single_repo:
            browser_repo_sections.append(browser_html)
            slack_sections.extend(slack_parts)
        else:
            # Multi-repo: wrap in accordion
            stats_parts = []
            if stats["prs"]:
                stats_parts.append(f'{stats["prs"]} PR')
            if stats["commits"]:
                stats_parts.append(f'{stats["commits"]} commits')
            if stats["reviews"]:
                stats_parts.append(f'{stats["reviews"]} reviews')
            stats_text = " / ".join(stats_parts) if stats_parts else "活動あり"
            browser_repo_sections.append(
                f'<details open class="repo-section">'
                f'<summary class="repo-summary">'
                f'📁 {escape(repo_key)} <span class="repo-stats">{stats_text}</span>'
                f'</summary>'
                f'<div class="repo-content">{browser_html}</div>'
                f'</details>'
            )
            slack_sections.append(
                f'<strong>📁 {escape(repo_key)}</strong><br>'
                + "<br>".join(slack_parts)
            )

    if not browser_repo_sections:
        browser_repo_sections.append('<p class="empty">活動なし</p>')

    # === Cross-repo sections (Project Status, Backlog, 継続作業) ===

    # --- Project Status ---
    project_html = ""
    if project_summary:
        status_counts = project_summary["status_counts"]
        in_progress = project_summary["in_progress"]
        in_review = project_summary["in_review"]
        blocked_items = project_summary["blocked"]
        today_ctx = project_summary["today_status_context"]
        next_actions = project_summary["next_actions"]

        # ステータス分布バー
        status_order = ["In progress", "In review", "Approved",
                        "Staging / Merged to Release", "Ready", "Backlog", "Done"]
        status_colors = {
            "In progress": "#1f883d", "In review": "#0969da",
            "Approved": "#8250df", "Staging / Merged to Release": "#bf8700",
            "Ready": "#656d76", "Backlog": "#d0d7de", "Done": "#8b949e",
        }
        total_items = sum(status_counts.values()) or 1
        bar_parts = []
        legend_parts = []
        for s in status_order:
            cnt = status_counts.get(s, 0)
            if cnt == 0:
                continue
            pct = cnt / total_items * 100
            color = status_colors.get(s, "#d0d7de")
            bar_parts.append(
                f'<div class="status-bar-seg" style="width:{pct:.1f}%;background:{color}" '
                f'title="{escape(s)}: {cnt}"></div>'
            )
            legend_parts.append(
                f'<span class="status-legend-item">'
                f'<span class="status-dot" style="background:{color}"></span>'
                f'{escape(s)}: {cnt}</span>'
            )

        distribution_html = (
            f'<div class="status-bar">{"".join(bar_parts)}</div>'
            f'<div class="status-legend">{"".join(legend_parts)}</div>'
        )

        # ステータス別タスク一覧 (ツリー対応統合表示)
        status_tasks_html = ""

        review_state_labels = {
            "APPROVED": ("承認済み", "badge-review-approved"),
            "CHANGES_REQUESTED": ("修正要求", "badge-review-changes"),
            "REVIEW_REQUIRED": ("レビュー待ち", "badge-review-pending"),
        }

        def _calc_elapsed(iso_since):
            """ISO datetimeから経過日数を計算"""
            if not iso_since:
                return None
            try:
                start = datetime.fromisoformat(iso_since.replace("Z", "+00:00")).date()
                today = datetime.strptime(date_str, "%Y-%m-%d").date()
                return (today - start).days
            except (ValueError, TypeError):
                return None

        def _elapsed_info_html(item, compact=False):
            """In Progress / In Review アイテムの経過日数・見積もり・レビュー状態のHTMLを生成"""
            status = item.get("status", "")
            if status not in ("In progress", "In review"):
                return ""
            parts = []
            if status == "In progress":
                elapsed = _calc_elapsed(item.get("in_progress_since", ""))
                if elapsed is not None:
                    parts.append(f'{elapsed}日経過' if not compact else f'{elapsed}d')
                est = item.get("estimate", "")
                if est:
                    parts.append(f'est:{est}')
            elif status == "In review":
                elapsed = _calc_elapsed(item.get("in_review_since", ""))
                if elapsed is not None:
                    parts.append(f'{elapsed}日経過' if not compact else f'{elapsed}d')
                rs = item.get("review_state", "")
                if rs:
                    label, badge_cls = review_state_labels.get(rs, (rs, "badge-plan"))
                    if compact:
                        parts.append(label)
                    else:
                        # バッジとして返す（partsとは別に）
                        elapsed_text = " / ".join(parts) if parts else ""
                        badge_html = f'<span class="badge {badge_cls}">{escape(label)}</span>'
                        if elapsed_text:
                            return f' {badge_html} <span class="elapsed-info">({elapsed_text})</span>'
                        return f' {badge_html}'
                est = item.get("estimate", "")
                if est:
                    parts.append(f'est:{est}')
            if not parts:
                return ""
            text = " / ".join(parts)
            return f' <span class="elapsed-info">({text})</span>' if not compact else f' <span class="elapsed-info">[{text}]</span>'

        def _elapsed_info_slack(item):
            """In Progress / In Review アイテムの経過日数・見積もり・レビュー状態のSlackテキスト"""
            status = item.get("status", "")
            if status not in ("In progress", "In review"):
                return ""
            parts = []
            if status == "In progress":
                elapsed = _calc_elapsed(item.get("in_progress_since", ""))
                if elapsed is not None:
                    parts.append(f'{elapsed}日経過')
                est = item.get("estimate", "")
                if est:
                    parts.append(f'est:{est}')
            elif status == "In review":
                elapsed = _calc_elapsed(item.get("in_review_since", ""))
                if elapsed is not None:
                    parts.append(f'{elapsed}日経過')
                rs = item.get("review_state", "")
                if rs:
                    label = review_state_labels.get(rs, (rs,))[0]
                    parts.append(label)
                est = item.get("estimate", "")
                if est:
                    parts.append(f'est:{est}')
            if not parts:
                return ""
            return f' ({" / ".join(parts)})'

        if next_actions:
            tree_entries = build_tree_for_next_actions(next_actions, project_items or [])
            na_items = []
            category_badge_map = {
                "継続作業": "badge-continue",
                "本日未更新": "badge-pending",
                "ブロック中": "badge-blocked",
                "レビュー待ち": "badge-review",
                "レビュー依頼": "badge-review-requested",
                "本日活動": "badge-today-active",
            }
            status_badge_colors = {
                "In progress": ("#dafbe1", "#1a7f37"),
                "In review": ("#ddf4ff", "#0550ae"),
                "Approved": ("#e8def8", "#6e3fb5"),
                "Staging / Merged to Release": ("#fff3cd", "#856404"),
                "Ready": ("#e8ecf0", "#656d76"),
                "Backlog": ("#f0f0f0", "#8b949e"),
                "Done": ("#dafbe1", "#1a7f37"),
            }
            flat_count = 0
            for entry in tree_entries:
                if entry["type"] == "flat":
                    na = entry["action"]
                    item = na["item"]
                    category = na["category"]
                    reason = na["reason"]
                    badge_cls = category_badge_map.get(category, "badge-plan")
                    repo_label = ""
                    if not single_repo and item.get("repo"):
                        repo_label = f' <span class="plan-repo">[{escape(item["repo"])}]</span>'
                    proj_label = ""
                    if item.get("project_name"):
                        proj_label = f' <span class="plan-repo project-label">[{escape(item["project_name"])}]</span>'
                    parent_badge = ""
                    if item.get("is_parent"):
                        parent_badge = f' <span class="badge badge-parent">親課題({item["sub_issue_count"]})</span>'
                    elapsed_html = _elapsed_info_html(item)
                    # レビュー依頼のPRリンク
                    pr_link_html = ""
                    review_pr = item.get("review_pr")
                    if review_pr:
                        pr_link_html = (
                            f' <span class="plan-reason">'
                            f'← <a href="{escape(review_pr["url"])}">PR #{review_pr["number"]}</a>'
                            f'</span>'
                        )
                    na_items.append(
                        f'<li class="item">'
                        f'<span class="badge {badge_cls}">{escape(category)}</span>'
                        f'{parent_badge} '
                        f'<a href="{escape(item["url"])}">#{item["number"]} {escape(item["title"])}</a>'
                        f'{repo_label}{proj_label}{elapsed_html}{pr_link_html}'
                        f' <span class="plan-reason">({escape(reason)})</span></li>'
                    )
                    flat_count += 1
                elif entry["type"] == "tree":
                    parent = entry["parent"]
                    children = entry["children"]
                    parent_action = entry.get("action")
                    # 親のバッジ
                    if parent_action:
                        cat = parent_action["category"]
                        badge_cls = category_badge_map.get(cat, "badge-plan")
                        cat_badge = f'<span class="badge {badge_cls}">{escape(cat)}</span> '
                    else:
                        parent_status = parent.get("status", "")
                        if parent_status:
                            bg, fg = status_badge_colors.get(parent_status, ("#e8ecf0", "#656d76"))
                            cat_badge = f'<span class="badge" style="background:{bg};color:{fg}">{escape(parent_status)}</span> '
                        else:
                            cat_badge = '<span class="badge badge-parent">親課題</span> '
                    repo_label = ""
                    if not single_repo and parent.get("repo"):
                        repo_label = f' <span class="plan-repo">[{escape(parent["repo"])}]</span>'
                    proj_label = ""
                    if parent.get("project_name"):
                        proj_label = f' <span class="plan-repo project-label">[{escape(parent["project_name"])}]</span>'
                    # 子Issue一覧
                    child_items_html = []
                    for child in children:
                        c_status = child.get("status", "")
                        c_state = child.get("state", "")
                        status_label = ""
                        if c_status:
                            bg, fg = status_badge_colors.get(c_status, ("#e8ecf0", "#656d76"))
                            status_label = f'<span class="badge" style="background:{bg};color:{fg};font-size:0.78em">{escape(c_status)}</span> '
                        elif c_state == "CLOSED":
                            status_label = '<span class="badge badge-closed" style="font-size:0.78em">Closed</span> '
                        c_repo_label = ""
                        if not single_repo and child.get("repo") and child.get("repo") != parent.get("repo"):
                            c_repo_label = f' <span class="plan-repo">[{escape(child["repo"])}]</span>'
                        c_elapsed = _elapsed_info_html(child, compact=True)
                        child_items_html.append(
                            f'<li class="item tree-child">'
                            f'{status_label}'
                            f'<a href="{escape(child["url"])}">#{child["number"]} {escape(child["title"])}</a>'
                            f'{c_elapsed}{c_repo_label}</li>'
                        )
                    children_html = f'<ul class="tree-children">{"".join(child_items_html)}</ul>' if child_items_html else ""
                    na_items.append(
                        f'<li class="item tree-parent">'
                        f'{cat_badge}'
                        f'<a href="{escape(parent["url"])}">#{parent["number"]} {escape(parent["title"])}</a>'
                        f' <span class="badge badge-parent">子課題 {len(children)}</span>'
                        f'{repo_label}{proj_label}'
                        f'{children_html}</li>'
                    )
                    flat_count += 1

            status_tasks_html = (
                f'<h3>🗓 ステータス別タスク ({flat_count})</h3>'
                f'<ul class="tree-list">{"".join(na_items)}</ul>'
            )

        project_html = (
            f'<div class="project-status-section">'
            f'<h2>📊 Project Status</h2>'
            f'{distribution_html}'
            f'{status_tasks_html}'
            f'</div>'
        )

        # Slack: Project Status (ツリー対応統合ステータス別タスク)
        if next_actions:
            tree_entries_slack = tree_entries  # HTML用で構築済みのものを再利用
            slack_na = []
            for entry in tree_entries_slack:
                if entry["type"] == "flat":
                    na = entry["action"]
                    item = na["item"]
                    repo_prefix = f"[{item['repo']}] " if not single_repo and item.get("repo") else ""
                    parent_mark = f"[親課題({item['sub_issue_count']})] " if item.get("is_parent") else ""
                    s_elapsed = _elapsed_info_slack(item)
                    review_pr = item.get("review_pr")
                    pr_link_slack = ""
                    if review_pr:
                        pr_link_slack = f' ← <a href="{escape(review_pr["url"])}">PR #{review_pr["number"]}</a>'
                    slack_na.append(
                        f'<li>[{escape(na["category"])}] {escape(repo_prefix)}{escape(parent_mark)}'
                        f'<a href="{escape(item["url"])}">#{item["number"]} {escape(item["title"])}</a>'
                        f'{escape(s_elapsed)}{pr_link_slack}</li>'
                    )
                elif entry["type"] == "tree":
                    parent = entry["parent"]
                    children = entry["children"]
                    parent_action = entry.get("action")
                    cat_label = f"[{parent_action['category']}] " if parent_action else ""
                    repo_prefix = f"[{parent['repo']}] " if not single_repo and parent.get("repo") else ""
                    slack_na.append(
                        f'<li>{escape(cat_label)}{escape(repo_prefix)}'
                        f'<a href="{escape(parent["url"])}">#{parent["number"]} {escape(parent["title"])}</a>'
                        f' (子課題 {len(children)})</li>'
                    )
                    for ci, child in enumerate(children):
                        c_status = child.get("status") or child.get("state", "")
                        is_last = ci == len(children) - 1
                        branch = "\u2514" if is_last else "\u251c"
                        c_s_elapsed = _elapsed_info_slack(child)
                        slack_na.append(
                            f'<li>\u00a0\u00a0{branch} [{escape(c_status)}] '
                            f'<a href="{escape(child["url"])}">#{child["number"]} {escape(child["title"])}</a>'
                            f'{escape(c_s_elapsed)}</li>'
                        )
            slack_sections.append(
                f'<strong>📊 Project Status ({len(slack_na)})</strong>'
                f'<ul>{"".join(slack_na)}</ul>'
            )

    # --- Backlog ---
    backlog_html = ""
    if backlog_activities:
        items = []
        for a in backlog_activities:
            type_label = a["type"]
            preview = escape(a["comment_preview"].split("\n")[0])[:80] if a["comment_preview"] else ""
            if preview:
                preview = f' <span class="backlog-preview">— {preview}</span>'
            items.append(
                f'<li class="item">'
                f'<a href="{escape(a["url"])}">{escape(a["issue_key"])}</a> '
                f'{escape(a["summary"])} '
                f'<span class="badge badge-backlog">{escape(type_label)}</span>'
                f'{preview}</li>'
            )
        backlog_html = f'<h2>📎 Backlog ({len(backlog_activities)})</h2><ul>{"".join(items)}</ul>'

        # Slack
        slack_items = []
        for a in backlog_activities:
            preview = (a["comment_preview"].split("\n")[0])[:80] if a["comment_preview"] else ""
            preview_html = f" — {escape(preview)}" if preview else ""
            slack_items.append(
                f'<li>[{escape(a["type"])}] '
                f'<a href="{escape(a["url"])}">{escape(a["issue_key"])}</a> '
                f'{escape(a["summary"])}{preview_html}</li>'
            )
        slack_sections.append(
            f'<strong>📎 Backlog ({len(backlog_activities)})</strong>'
            f'<ul>{"".join(slack_items)}</ul>'
        )

    # --- フォールバック: 明日の予定 (Project データがない場合のみ) ---
    next_actions_html = ""
    if not project_summary and tomorrow_plan:
        # フォールバック: 従来の明日の予定
        items = []
        for item in tomorrow_plan:
            reason = f' <span class="plan-reason">({escape(item["reason"])})</span>' if item.get("reason") else ""
            repo_label = ""
            if not single_repo and item.get("repo"):
                repo_label = f' <span class="plan-repo">[{escape(item["repo"])}]</span>'
            items.append(
                f'<li class="item">'
                f'<span class="badge badge-plan">{escape(item["category"])}</span> '
                f'<a href="{escape(item["url"])}">{escape(item["text"])}</a>'
                f'{repo_label}{reason}</li>'
            )
        next_actions_html = f'<h2>🗓 明日の予定 ({len(tomorrow_plan)})</h2><ul>{"".join(items)}</ul>'

        # Slack
        slack_items = []
        for item in tomorrow_plan:
            repo_prefix = f"[{item['repo']}] " if not single_repo and item.get("repo") else ""
            slack_items.append(
                f'<li>[{escape(item["category"])}] {escape(repo_prefix)}'
                f'<a href="{escape(item["url"])}">{escape(item["text"])}</a></li>'
            )
        slack_sections.append(
            f'<strong>🗓 明日の予定 ({len(tomorrow_plan)})</strong>'
            f'<ul>{"".join(slack_items)}</ul>'
        )

    # === 本日実績: Project→親課題→子課題 階層フォーマット ===
    if project_items:
        # 今日活動のあったIssue/PR番号をリポごとに収集
        today_active = {}  # (repo, number) -> activity_type
        for repo_name, rd in per_repo_data.items():
            for pr in rd.get("prs", []):
                today_active[(repo_name, pr["number"])] = "PR"
                for inum in pr.get("issues", []):
                    today_active[(repo_name, int(inum))] = "PR関連"
            for ic in rd.get("issue_comments", []):
                today_active[(repo_name, ic["issue_number"])] = "コメント"
            for ci in rd.get("created_issues", []):
                today_active[(repo_name, ci["number"])] = "新規起票"
            for mp in rd.get("merged_prs", []):
                today_active[(repo_name, mp["number"])] = "マージ"

        # project_itemsから今日活動のあるものを抽出・Project別にグループ化
        proj_groups = {}  # project_name -> list of items
        for it in project_items:
            repo = it.get("repo", "")
            num = it.get("number")
            if (repo, num) in today_active:
                pname = it.get("project_name") or repo
                proj_groups.setdefault(pname, []).append(it)

        # 活動はあるがProjectに紐づかないリポのPR/Issue
        proj_repos = set()
        for it in project_items:
            proj_repos.add(it.get("repo", ""))
        for repo_name, rd in per_repo_data.items():
            if repo_name in proj_repos:
                continue
            repo_items = []
            for mp in rd.get("merged_prs", []):
                repo_items.append({
                    "number": mp["number"], "title": mp.get("title", ""),
                    "url": mp.get("url", ""), "is_pr": True,
                    "repo": repo_name, "_activity": "マージ",
                })
            for ci in rd.get("created_issues", []):
                repo_items.append({
                    "number": ci["number"], "title": ci.get("title", ""),
                    "url": ci.get("url", ""), "is_pr": False,
                    "repo": repo_name, "_activity": "新規起票",
                })
            for pr in rd.get("prs", []):
                if not any(ri["number"] == pr["number"] for ri in repo_items):
                    repo_items.append({
                        "number": pr["number"], "title": pr.get("title", ""),
                        "url": pr.get("url", ""), "is_pr": True,
                        "repo": repo_name, "_activity": "PR",
                    })
            if repo_items:
                proj_groups.setdefault(repo_name, []).extend(repo_items)

        # Slack用HTML構築
        slack_proj_parts = [f'<strong>本日実績</strong>']
        for pname in sorted(proj_groups.keys()):
            items = proj_groups[pname]
            slack_proj_parts.append(f'<br><strong>{escape(pname)}</strong>')

            # 親課題→子課題のツリー構築
            parents = {}  # parent_url -> {"item": parent_item, "children": []}
            orphans = []
            for it in items:
                parent = it.get("parent")
                if parent:
                    purl = parent.get("url", "")
                    if purl not in parents:
                        parents[purl] = {"item": parent, "children": []}
                    parents[purl]["children"].append(it)
                elif it.get("is_parent"):
                    url = it.get("url", "")
                    if url not in parents:
                        parents[url] = {"item": it, "children": []}
                    else:
                        parents[url]["item"] = it
                else:
                    orphans.append(it)

            # 親課題+子課題を出力
            for purl, tree in parents.items():
                p = tree["item"]
                p_title = p.get("title", "")
                p_num = p.get("number", "")
                p_link_url = p.get("url", "")
                slack_proj_parts.append(
                    f'<br>{escape(p_title)} '
                    f'(<a href="{escape(p_link_url)}">#{p_num}</a>)'
                )
                for child in tree["children"]:
                    c_title = child.get("title", "")
                    c_num = child.get("number", "")
                    c_url = child.get("url", "")
                    c_state = child.get("state", "")
                    c_status = child.get("status", "")
                    c_activity = today_active.get((child.get("repo", ""), c_num), child.get("_activity", ""))
                    if c_state == "CLOSED" or c_activity == "完了確認":
                        status_str = "完了"
                    elif c_status == "In progress":
                        status_str = "進行中"
                    elif c_status == "In review":
                        status_str = "レビュー中"
                    elif child.get("blocked") == "Yes":
                        status_str = "ブロック中"
                    elif c_activity == "新規起票":
                        status_str = "新規起票"
                    elif c_activity == "マージ":
                        status_str = "マージ"
                    elif c_status:
                        status_str = c_status
                    else:
                        status_str = c_activity
                    slack_proj_parts.append(
                        f'- {escape(c_title)} '
                        f'(<a href="{escape(c_url)}">#{c_num}</a>)'
                        f' → {escape(status_str)}'
                    )

            # 親なし単独アイテム
            for it in orphans:
                t = it.get("title", "")
                n = it.get("number", "")
                u = it.get("url", "")
                state = it.get("state", "")
                status = it.get("status", "")
                activity = today_active.get((it.get("repo", ""), n), it.get("_activity", ""))
                if state == "CLOSED":
                    s = "完了"
                elif it.get("is_pr") and (state == "MERGED" or activity == "マージ"):
                    s = "マージ"
                elif status == "In progress":
                    s = "進行中"
                elif status == "In review":
                    s = "レビュー中"
                elif it.get("blocked") == "Yes":
                    s = "ブロック中"
                elif activity == "新規起票":
                    s = "新規起票"
                elif activity:
                    s = activity
                elif status:
                    s = status
                else:
                    s = ""
                suffix = f" → {escape(s)}" if s else ""
                slack_proj_parts.append(
                    f'- {escape(t)} '
                    f'(<a href="{escape(u)}">#{n}</a>)'
                    f'{suffix}'
                )

        slack_html = "<br>".join(slack_proj_parts)
    else:
        slack_html = "<br>".join(slack_sections)

    # === Document assembly ===
    if single_repo:
        rd = list(per_repo_data.values())[0]
        doc_title = f'日報 {date_str} - {rd["owner"]}/{rd["repo"]}'
        repo_url = f'https://github.com/{rd["owner"]}/{rd["repo"]}'
        meta_html = f'<a href="{repo_url}">{rd["owner"]}/{rd["repo"]}</a> | @{username}'
    else:
        doc_title = f'日報 {date_str}'
        meta_html = f'{escape(org)} | @{username}'

    repo_content = "\n    ".join(browser_repo_sections)

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{doc_title}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
    max-width: 800px; margin: 0 auto; padding: 24px;
    color: #24292f; background: #fff; line-height: 1.6;
  }}
  h1 {{ font-size: 1.5em; border-bottom: 2px solid #d0d7de; padding-bottom: 8px; margin-bottom: 4px; }}
  .meta {{ color: #656d76; font-size: 0.9em; margin-bottom: 20px; }}
  .meta a {{ color: #0969da; text-decoration: none; }}
  h2 {{ font-size: 1.15em; margin-top: 28px; margin-bottom: 12px; color: #24292f; }}
  h3 {{ font-size: 1.05em; margin-top: 20px; margin-bottom: 10px; color: #24292f; }}
  ul {{ list-style: none; padding: 0; }}

  /* リポジトリアコーディオン */
  .repo-section {{
    margin: 16px 0; border: 1px solid #d0d7de; border-radius: 8px;
    overflow: hidden;
  }}
  .repo-summary {{
    padding: 12px 16px; background: #f6f8fa; cursor: pointer;
    font-weight: 600; font-size: 1.1em; list-style: none;
    display: flex; align-items: center; gap: 8px;
  }}
  .repo-summary::-webkit-details-marker {{ display: none; }}
  .repo-section[open] > .repo-summary::before {{ content: "▼"; font-size: 0.7em; color: #656d76; }}
  .repo-section:not([open]) > .repo-summary::before {{ content: "▶"; font-size: 0.7em; color: #656d76; }}
  .repo-stats {{ font-size: 0.75em; font-weight: 400; color: #656d76; margin-left: auto; }}
  .repo-content {{ padding: 8px 16px 16px; }}

  /* PR グループ */
  .pr-group {{
    margin: 12px 0; padding: 12px 16px;
    border: 1px solid #d0d7de; border-radius: 6px; background: #f6f8fa;
  }}
  .pr-group.orphan {{ border-style: dashed; background: #fff8f0; }}
  .pr-header {{ font-weight: 600; }}
  .pr-header a {{ color: #0969da; text-decoration: none; }}
  .pr-header a:hover {{ text-decoration: underline; }}
  .orphan-header {{ color: #656d76; }}
  .related-issues {{ font-size: 0.88em; color: #656d76; margin-top: 2px; }}
  .related-issues a {{ color: #0969da; }}
  .inline-issue {{ font-size: 0.88em; color: #656d76; }}
  .inline-issue a {{ color: #0969da; }}

  /* コミットリスト（PR内） */
  .pr-commit-list {{
    margin-top: 8px; padding-top: 8px;
    border-top: 1px solid #d8dee4;
  }}
  .commit-item {{
    padding: 3px 0; font-size: 0.92em; color: #24292f;
  }}
  .commit-item a {{ color: #0969da; text-decoration: none; }}
  .sha {{
    font-family: SFMono-Regular, Consolas, monospace;
    font-size: 0.85em; background: #e8ecf0; padding: 1px 5px; border-radius: 3px;
  }}

  /* バッジ */
  .badge {{
    display: inline-block; font-size: 0.75em; font-weight: 600;
    padding: 1px 8px; border-radius: 12px; margin-right: 4px;
  }}
  .badge-open {{ background: #dafbe1; color: #1a7f37; }}
  .badge-merged {{ background: #ddf4ff; color: #0550ae; }}
  .badge-closed {{ background: #ffd8d3; color: #cf222e; }}
  .badge-backlog {{ background: #e8f0fe; color: #42526e; }}
  .backlog-preview {{ font-size: 0.85em; color: #656d76; }}
  .badge-plan {{ background: #fff3cd; color: #856404; }}
  .badge-continue {{ background: #dafbe1; color: #1a7f37; }}
  .badge-pending {{ background: #fff3cd; color: #856404; }}
  .badge-blocked {{ background: #ffd8d3; color: #cf222e; }}
  .badge-review {{ background: #ddf4ff; color: #0550ae; }}
  .badge-today-active {{ background: #fff8c5; color: #6a5300; }}
  .badge-parent {{ background: #e8def8; color: #6e3fb5; font-size: 0.78em; }}
  .tree-list {{ list-style: none; padding-left: 0; }}
  .tree-list > li {{ margin-bottom: 4px; }}
  .tree-parent {{ list-style: none; }}
  .tree-children {{
    list-style: none; padding-left: 20px; margin-top: 4px;
    border-left: 2px solid #d0d7de;
  }}
  .tree-children li {{ padding: 2px 0 2px 8px; font-size: 0.92em; }}
  .tree-child {{ color: #24292f; }}
  .elapsed-info {{ font-size: 0.82em; color: #cf222e; font-weight: 500; }}
  .badge-review-approved {{ background: #dafbe1; color: #1a7f37; font-size: 0.78em; }}
  .badge-review-changes {{ background: #ffd8d3; color: #cf222e; font-size: 0.78em; }}
  .badge-review-pending {{ background: #ddf4ff; color: #0550ae; font-size: 0.78em; }}
  .badge-review-requested {{ background: #fbefff; color: #8250df; }}
  .plan-reason {{ font-size: 0.82em; color: #8b949e; }}
  .plan-repo {{ font-size: 0.82em; color: #656d76; font-weight: 500; }}
  .pr-label {{
    display: inline-block; font-size: 0.75em; font-weight: 500;
    padding: 1px 6px; border-radius: 10px; background: #e8ecf0; color: #24292f;
  }}

  /* Project Status */
  .project-status-section {{
    margin: 20px 0; padding: 16px;
    border: 1px solid #d0d7de; border-radius: 8px; background: #f6f8fa;
  }}
  .project-status-section h2 {{ margin-top: 0; }}
  .status-bar {{
    display: flex; height: 12px; border-radius: 6px; overflow: hidden;
    margin: 12px 0 8px; background: #e8ecf0;
  }}
  .status-bar-seg {{ min-width: 3px; }}
  .status-legend {{
    display: flex; flex-wrap: wrap; gap: 12px;
    font-size: 0.82em; color: #656d76; margin-bottom: 12px;
  }}
  .status-legend-item {{ display: flex; align-items: center; gap: 4px; }}
  .status-dot {{
    display: inline-block; width: 8px; height: 8px;
    border-radius: 50%;
  }}
  .today-active-mark {{
    color: #bf8700; font-size: 0.85em; font-weight: 600;
  }}

  /* その他 */
  .item {{ padding: 6px 0; border-bottom: 1px solid #f0f0f0; }}
  .item a {{ color: #0969da; text-decoration: none; }}
  .item a:hover {{ text-decoration: underline; }}
  .state {{ color: #656d76; font-size: 0.85em; }}
  .empty {{ color: #656d76; font-style: italic; padding: 4px 0; }}
  .copy-area {{
    margin-top: 32px; padding-top: 16px; border-top: 2px solid #d0d7de;
    text-align: center;
  }}
  .copy-btn {{
    background: #0969da; color: #fff; border: none;
    padding: 8px 24px; border-radius: 6px; font-size: 0.95em;
    cursor: pointer; font-weight: 500;
  }}
  .copy-btn:hover {{ background: #0550ae; }}
  .copy-btn.copied {{ background: #1a7f37; }}
  #slack-content {{ display: none; }}
</style>
</head>
<body>
  <div id="report-content">
    <h1>日報 {date_str}</h1>
    <p class="meta">{meta_html}</p>
    {repo_content}
    {project_html}
    {backlog_html}
    {next_actions_html}
  </div>

  <div id="slack-content">{slack_html}</div>

  <div class="copy-area">
    <button class="copy-btn" onclick="copyForSlack()">📋 Slackにコピー</button>
  </div>

  <script>
    function copyForSlack() {{
      const slackEl = document.getElementById('slack-content');
      const htmlContent = slackEl.innerHTML;
      const plainText = slackEl.innerText;

      const clipboardItem = new ClipboardItem({{
        'text/html': new Blob([htmlContent], {{ type: 'text/html' }}),
        'text/plain': new Blob([plainText], {{ type: 'text/plain' }}),
      }});

      navigator.clipboard.write([clipboardItem]).then(() => {{
        showCopied();
      }}).catch(() => {{
        slackEl.style.display = 'block';
        const range = document.createRange();
        range.selectNodeContents(slackEl);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(range);
        document.execCommand('copy');
        sel.removeAllRanges();
        slackEl.style.display = 'none';
        showCopied();
      }});
    }}

    function showCopied() {{
      const btn = document.querySelector('.copy-btn');
      btn.textContent = '✅ コピーしました';
      btn.classList.add('copied');
      setTimeout(() => {{
        btn.textContent = '📋 Slackにコピー';
        btn.classList.remove('copied');
      }}, 2000);
    }}
  </script>
</body>
</html>"""
    return html


def generate_team_html(date_str, org, members_data, project_items, status_counts):
    """全メンバー統合HTML日報を生成する

    Args:
        date_str: 対象日
        org: org名
        members_data: [{username, project_summary, per_repo_data, review_requested_prs}, ...]
        project_items: get_project_items() の全データ
        status_counts: ステータス分布 (dict)
    """
    status_order = ["In progress", "In review", "Approved",
                    "Staging / Merged to Release", "Ready", "Backlog", "Done"]
    status_colors = {
        "In progress": "#1f883d", "In review": "#0969da",
        "Approved": "#8250df", "Staging / Merged to Release": "#bf8700",
        "Ready": "#656d76", "Backlog": "#d0d7de", "Done": "#8b949e",
    }
    category_badge_map = {
        "継続作業": "badge-continue",
        "本日未更新": "badge-pending",
        "ブロック中": "badge-blocked",
        "レビュー待ち": "badge-review",
        "レビュー依頼": "badge-review-requested",
        "本日活動": "badge-today-active",
    }
    status_badge_colors = {
        "In progress": ("#dafbe1", "#1a7f37"),
        "In review": ("#ddf4ff", "#0550ae"),
        "Approved": ("#e8def8", "#6e3fb5"),
        "Staging / Merged to Release": ("#fff3cd", "#856404"),
        "Ready": ("#e8ecf0", "#656d76"),
        "Backlog": ("#f0f0f0", "#8b949e"),
        "Done": ("#dafbe1", "#1a7f37"),
    }
    review_state_labels = {
        "APPROVED": ("承認済み", "badge-review-approved"),
        "CHANGES_REQUESTED": ("修正要求", "badge-review-changes"),
        "REVIEW_REQUIRED": ("レビュー待ち", "badge-review-pending"),
    }

    def _calc_elapsed(iso_since):
        if not iso_since:
            return None
        try:
            start = datetime.fromisoformat(iso_since.replace("Z", "+00:00")).date()
            today = datetime.strptime(date_str, "%Y-%m-%d").date()
            return (today - start).days
        except (ValueError, TypeError):
            return None

    def _item_html(item, category=None, reason=None, show_repo=True):
        """アイテム1行のHTML"""
        parts = []
        if category:
            badge_cls = category_badge_map.get(category, "badge-plan")
            parts.append(f'<span class="badge {badge_cls}">{escape(category)}</span>')

        parts.append(f' <a href="{escape(item["url"])}">#{item["number"]} {escape(item["title"])}</a>')

        if show_repo and item.get("repo"):
            parts.append(f' <span class="plan-repo">[{escape(item["repo"])}]</span>')

        # 経過日数
        status = item.get("status", "")
        if status == "In progress":
            elapsed = _calc_elapsed(item.get("in_progress_since", ""))
            est = item.get("estimate", "")
            info_parts = []
            if elapsed is not None:
                info_parts.append(f'{elapsed}日経過')
            if est:
                info_parts.append(f'est:{est}')
            if info_parts:
                parts.append(f' <span class="elapsed-info">({" / ".join(info_parts)})</span>')
        elif status == "In review":
            elapsed = _calc_elapsed(item.get("in_review_since", ""))
            rs = item.get("review_state", "")
            info_parts = []
            if elapsed is not None:
                info_parts.append(f'{elapsed}日経過')
            if rs:
                label, badge_cls = review_state_labels.get(rs, (rs, "badge-plan"))
                parts.append(f' <span class="badge {badge_cls}">{escape(label)}</span>')
            if info_parts:
                parts.append(f' <span class="elapsed-info">({" / ".join(info_parts)})</span>')

        # PRリンク
        review_pr = item.get("review_pr")
        if review_pr:
            parts.append(
                f' <span class="plan-reason">'
                f'← <a href="{escape(review_pr["url"])}">PR #{review_pr["number"]}</a>'
                f'</span>'
            )

        if reason:
            parts.append(f' <span class="plan-reason">({escape(reason)})</span>')

        return f'<li class="item">{"".join(parts)}</li>'

    # === ステータス分布バー ===
    total_items = sum(status_counts.values()) or 1
    bar_parts = []
    legend_parts = []
    for s in status_order:
        cnt = status_counts.get(s, 0)
        if cnt == 0:
            continue
        pct = cnt / total_items * 100
        color = status_colors.get(s, "#d0d7de")
        bar_parts.append(
            f'<div class="status-bar-seg" style="width:{pct:.1f}%;background:{color}" '
            f'title="{escape(s)}: {cnt}"></div>'
        )
        legend_parts.append(
            f'<span class="status-legend-item">'
            f'<span class="status-dot" style="background:{color}"></span>'
            f'{escape(s)}: {cnt}</span>'
        )
    distribution_html = (
        f'<div class="status-bar">{"".join(bar_parts)}</div>'
        f'<div class="status-legend">{"".join(legend_parts)}</div>'
    )

    # === メンバーごとのセクション ===
    member_sections = []
    slack_sections = [
        f'<strong>チーム日報 {date_str}</strong><br>{escape(org)}'
    ]

    for md in members_data:
        uname = md["username"]
        ps = md.get("project_summary")
        per_repo = md.get("per_repo_data", {})

        # 活動件数集計
        activity_count = 0
        for rd in per_repo.values():
            activity_count += len(rd.get("prs", []))
            activity_count += len(rd.get("orphan_commits", []))
            activity_count += sum(len(v) for v in rd.get("review_comments", {}).values())
            activity_count += len(rd.get("reviewed_prs", []))
            activity_count += len(rd.get("issue_comments", []))
            activity_count += len(rd.get("created_issues", []))

        next_actions = ps["next_actions"] if ps else []
        tree_entries = build_tree_for_next_actions(next_actions, project_items) if next_actions else []

        has_content = bool(next_actions) or activity_count > 0

        # ヘッダーのサマリ
        summary_parts = []
        if ps:
            ip = len(ps["in_progress"])
            ir = len(ps["in_review"])
            bl = len(ps["blocked"])
            rr = len(md.get("review_requested_prs", []))
            if ip:
                summary_parts.append(f'{ip} In Progress')
            if ir:
                summary_parts.append(f'{ir} In Review')
            if bl:
                summary_parts.append(f'{bl} Blocked')
            if rr:
                summary_parts.append(f'{rr} Review Req')
        if activity_count:
            summary_parts.append(f'{activity_count} activities')
        summary_text = " / ".join(summary_parts) if summary_parts else "活動なし"

        # メンバー内HTML
        inner_parts = []

        # ステータス別タスク
        if tree_entries:
            task_items = []
            for entry in tree_entries:
                if entry["type"] == "flat":
                    na = entry["action"]
                    task_items.append(_item_html(na["item"], na["category"], na["reason"]))
                elif entry["type"] == "tree":
                    parent = entry["parent"]
                    children = entry["children"]
                    pa = entry.get("action")
                    if pa:
                        cat_badge = f'<span class="badge {category_badge_map.get(pa["category"], "badge-plan")}">{escape(pa["category"])}</span> '
                    else:
                        ps_status = parent.get("status", "")
                        if ps_status:
                            bg, fg = status_badge_colors.get(ps_status, ("#e8ecf0", "#656d76"))
                            cat_badge = f'<span class="badge" style="background:{bg};color:{fg}">{escape(ps_status)}</span> '
                        else:
                            cat_badge = '<span class="badge badge-parent">親課題</span> '
                    repo_label = f' <span class="plan-repo">[{escape(parent.get("repo", ""))}]</span>' if parent.get("repo") else ""
                    child_html_items = []
                    for child in children:
                        c_status = child.get("status", "")
                        c_state = child.get("state", "")
                        sl = ""
                        if c_status:
                            bg, fg = status_badge_colors.get(c_status, ("#e8ecf0", "#656d76"))
                            sl = f'<span class="badge" style="background:{bg};color:{fg};font-size:0.78em">{escape(c_status)}</span> '
                        elif c_state == "CLOSED":
                            sl = '<span class="badge badge-closed" style="font-size:0.78em">Closed</span> '
                        c_elapsed = ""
                        if c_status == "In progress":
                            e = _calc_elapsed(child.get("in_progress_since", ""))
                            if e is not None:
                                c_elapsed = f' <span class="elapsed-info">[{e}d]</span>'
                        elif c_status == "In review":
                            e = _calc_elapsed(child.get("in_review_since", ""))
                            if e is not None:
                                c_elapsed = f' <span class="elapsed-info">[{e}d]</span>'
                        child_html_items.append(
                            f'<li class="item tree-child">{sl}'
                            f'<a href="{escape(child["url"])}">#{child["number"]} {escape(child["title"])}</a>'
                            f'{c_elapsed}</li>'
                        )
                    children_ul = f'<ul class="tree-children">{"".join(child_html_items)}</ul>' if child_html_items else ""
                    task_items.append(
                        f'<li class="item tree-parent">{cat_badge}'
                        f'<a href="{escape(parent["url"])}">#{parent["number"]} {escape(parent["title"])}</a>'
                        f' <span class="badge badge-parent">子課題 {len(children)}</span>'
                        f'{repo_label}{children_ul}</li>'
                    )
            inner_parts.append(f'<ul class="tree-list">{"".join(task_items)}</ul>')

        # リポ別活動サマリ（コミット履歴含む）
        for repo_key in sorted(per_repo.keys()):
            rd = per_repo[repo_key]
            r_owner = rd.get("owner", "")
            r_repo = rd.get("repo", repo_key)
            repo_url = f"https://github.com/{r_owner}/{r_repo}" if r_owner else ""
            activity_items = []
            # PR一覧
            for pr in rd.get("prs", []):
                pr_line = f'🔀 <a href="{escape(pr["url"])}">#{pr["number"]} {escape(pr["title"])}</a>'
                # PRに紐づくコミット
                pr_commits_data = rd.get("pr_commits", {})
                pr_commits_list = pr_commits_data.get(pr["number"], [])
                if pr_commits_list:
                    commit_lines = []
                    for c in pr_commits_list:
                        c_url = c.get("url") or f"{repo_url}/commit/{c['full_sha']}" if repo_url else ""
                        c_link = f'<a href="{escape(c_url)}">{c["sha"]}</a>' if c_url else c["sha"]
                        commit_lines.append(f'<li class="commit-item">{c_link} {escape(c["message"])}</li>')
                    pr_line += f'<ul class="commit-list">{"".join(commit_lines)}</ul>'
                activity_items.append(pr_line)
            # レビュー・マージ
            for rp in rd.get("reviewed_prs", []):
                activity_items.append(f'👀 <a href="{escape(rp["url"])}">#{rp["number"]}</a> をレビュー')
            for mp in rd.get("merged_prs", []):
                activity_items.append(f'✅ <a href="{escape(mp["url"])}">#{mp["number"]}</a> をマージ')
            # PR不在のコミット
            orphan_commits = rd.get("orphan_commits", [])
            if orphan_commits:
                commit_lines = []
                for c in orphan_commits:
                    c_url = c.get("url") or f"{repo_url}/commit/{c['full_sha']}" if repo_url else ""
                    c_link = f'<a href="{escape(c_url)}">{c["sha"]}</a>' if c_url else c["sha"]
                    commit_lines.append(f'<li class="commit-item">{c_link} {escape(c["message"])}</li>')
                activity_items.append(
                    f'💾 PR不在のコミット ({len(orphan_commits)})'
                    f'<ul class="commit-list">{"".join(commit_lines)}</ul>'
                )
            if activity_items:
                inner_parts.append(
                    f'<div class="team-repo-activity">'
                    f'<strong>📁 {escape(repo_key)}</strong>'
                    f'<ul class="activity-list">{"".join(f"<li>{a}</li>" for a in activity_items)}</ul>'
                    f'</div>'
                )

        inner_html = "".join(inner_parts) if inner_parts else '<p class="empty">活動・タスクなし</p>'

        open_attr = ' open' if has_content else ''
        member_sections.append(
            f'<details{open_attr} class="member-section">'
            f'<summary class="member-summary">'
            f'👤 {escape(uname)} <span class="repo-stats">{summary_text}</span>'
            f'</summary>'
            f'{inner_html}'
            f'</details>'
        )

        # Slack
        if has_content:
            slack_parts = [f'\n<strong>👤 {escape(uname)}</strong>']
            if tree_entries:
                for entry in tree_entries:
                    if entry["type"] == "flat":
                        na = entry["action"]
                        item = na["item"]
                        rp = f"[{item['repo']}] " if item.get("repo") else ""
                        elapsed_parts = []
                        if item.get("status") == "In progress":
                            e = _calc_elapsed(item.get("in_progress_since", ""))
                            if e is not None:
                                elapsed_parts.append(f'{e}日経過')
                        elif item.get("status") == "In review":
                            e = _calc_elapsed(item.get("in_review_since", ""))
                            if e is not None:
                                elapsed_parts.append(f'{e}日経過')
                        elapsed_text = f' ({", ".join(elapsed_parts)})' if elapsed_parts else ""
                        pr_link = ""
                        rpr = item.get("review_pr")
                        if rpr:
                            pr_link = f' ← <a href="{escape(rpr["url"])}">PR #{rpr["number"]}</a>'
                        slack_parts.append(
                            f'[{escape(na["category"])}] {escape(rp)}'
                            f'<a href="{escape(item["url"])}">#{item["number"]} {escape(item["title"])}</a>'
                            f'{escape(elapsed_text)}{pr_link}'
                        )
                    elif entry["type"] == "tree":
                        parent = entry["parent"]
                        children = entry["children"]
                        pa = entry.get("action")
                        cat_l = f"[{pa['category']}] " if pa else ""
                        rp = f"[{parent['repo']}] " if parent.get("repo") else ""
                        slack_parts.append(
                            f'{escape(cat_l)}{escape(rp)}'
                            f'<a href="{escape(parent["url"])}">#{parent["number"]} {escape(parent["title"])}</a>'
                            f' (子課題 {len(children)})'
                        )
                        for ci, ch in enumerate(children):
                            cs = ch.get("status") or ch.get("state", "")
                            branch = "\u2514" if ci == len(children) - 1 else "\u251c"
                            slack_parts.append(f'\u00a0\u00a0{branch} [{escape(cs)}] '
                                f'<a href="{escape(ch["url"])}">#{ch["number"]} {escape(ch["title"])}</a>')
            # Slack: リポ別コミット
            for repo_key in sorted(per_repo.keys()):
                rd = per_repo[repo_key]
                commit_count = len(rd.get("orphan_commits", [])) + sum(len(v) for v in rd.get("pr_commits", {}).values())
                if commit_count > 0:
                    slack_parts.append(f'📁 {escape(repo_key)} ({commit_count} commits)')
                    for pr in rd.get("prs", []):
                        pr_commits_list = rd.get("pr_commits", {}).get(pr["number"], [])
                        if pr_commits_list:
                            slack_parts.append(f'  🔀 <a href="{escape(pr["url"])}">#{pr["number"]}</a> ({len(pr_commits_list)} commits)')
                    orphans = rd.get("orphan_commits", [])
                    if orphans:
                        slack_parts.append(f'  💾 PR不在 ({len(orphans)} commits)')
            slack_sections.append("<br>".join(slack_parts))

    html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>チーム日報 {date_str} - {escape(org)}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, sans-serif;
    max-width: 900px; margin: 0 auto; padding: 20px; color: #24292f; }}
  h1 {{ border-bottom: 1px solid #d0d7de; padding-bottom: 8px; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .badge {{
    display: inline-block; font-size: 0.82em; font-weight: 600;
    padding: 2px 8px; border-radius: 12px; white-space: nowrap;
  }}
  .badge-continue {{ background: #dafbe1; color: #1a7f37; }}
  .badge-pending {{ background: #fff3cd; color: #856404; }}
  .badge-blocked {{ background: #ffd8d3; color: #cf222e; }}
  .badge-review {{ background: #ddf4ff; color: #0550ae; }}
  .badge-review-requested {{ background: #fbefff; color: #8250df; }}
  .badge-today-active {{ background: #fff8c5; color: #6a5300; }}
  .badge-parent {{ background: #e8def8; color: #6e3fb5; font-size: 0.78em; }}
  .badge-closed {{ background: #ffd8d3; color: #cf222e; }}
  .badge-review-approved {{ background: #dafbe1; color: #1a7f37; font-size: 0.78em; }}
  .badge-review-changes {{ background: #ffd8d3; color: #cf222e; font-size: 0.78em; }}
  .badge-review-pending {{ background: #ddf4ff; color: #0550ae; font-size: 0.78em; }}
  .elapsed-info {{ font-size: 0.82em; color: #cf222e; font-weight: 500; }}
  .plan-reason {{ font-size: 0.82em; color: #8b949e; }}
  .plan-repo {{ font-size: 0.82em; color: #656d76; font-weight: 500; }}
  .empty {{ color: #8b949e; font-style: italic; }}
  .project-status-section {{
    margin: 20px 0; padding: 16px;
    border: 1px solid #d0d7de; border-radius: 8px; background: #f6f8fa;
  }}
  .status-bar {{
    display: flex; height: 12px; border-radius: 6px; overflow: hidden;
    margin: 12px 0 8px; background: #e8ecf0;
  }}
  .status-bar-seg {{ min-width: 3px; }}
  .status-legend {{ display: flex; flex-wrap: wrap; gap: 12px; font-size: 0.85em; color: #656d76; }}
  .status-legend-item {{ display: flex; align-items: center; gap: 4px; }}
  .status-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; }}
  .member-section {{
    margin: 12px 0; border: 1px solid #d0d7de; border-radius: 8px;
  }}
  .member-summary {{
    padding: 10px 16px; cursor: pointer; font-weight: 600;
    background: #f6f8fa; border-radius: 8px;
  }}
  .member-section[open] .member-summary {{ border-radius: 8px 8px 0 0; border-bottom: 1px solid #d0d7de; }}
  .member-section > *:not(summary) {{ padding: 8px 16px; }}
  .repo-stats {{ font-weight: 400; font-size: 0.85em; color: #656d76; }}
  .tree-list {{ list-style: none; padding-left: 0; }}
  .tree-list > li {{ margin-bottom: 4px; }}
  .tree-children {{
    list-style: none; padding-left: 20px; margin-top: 4px;
    border-left: 2px solid #d0d7de;
  }}
  .tree-children li {{ padding: 2px 0 2px 8px; font-size: 0.92em; }}
  .item {{ margin: 3px 0; }}
  .team-repo-activity {{ font-size: 0.9em; margin: 4px 0; padding: 4px 0; border-top: 1px solid #eee; }}
  .activity-list {{ margin: 4px 0; padding-left: 20px; }}
  .activity-list > li {{ margin: 2px 0; }}
  .commit-list {{ margin: 2px 0 4px; padding-left: 16px; font-size: 0.9em; color: #656d76; }}
  .commit-item {{ margin: 1px 0; }}
  .commit-item a {{ font-family: monospace; font-size: 0.95em; }}
  #slack-content {{ display: none; }}
  .copy-area {{ text-align: center; margin: 20px 0; }}
  .copy-btn {{
    background: #0969da; color: white; border: none; padding: 8px 20px;
    border-radius: 6px; cursor: pointer; font-size: 14px;
  }}
  .copy-btn:hover {{ background: #0550ae; }}
</style>
</head>
<body>
  <h1>チーム日報 {date_str} <span style="font-size:0.6em;color:#656d76">{escape(org)}</span></h1>

  <div class="project-status-section">
    <h2>📊 Project Status</h2>
    {distribution_html}
  </div>

  {"".join(member_sections)}

  <div id="slack-content">{"<br>".join(slack_sections)}</div>

  <div class="copy-area">
    <button class="copy-btn" onclick="copyForSlack()">📋 Slackにコピー</button>
  </div>

  <script>
    function copyForSlack() {{
      const slackEl = document.getElementById('slack-content');
      const htmlContent = slackEl.innerHTML;
      const plainText = slackEl.innerText;
      const clipboardItem = new ClipboardItem({{
        'text/html': new Blob([htmlContent], {{ type: 'text/html' }}),
        'text/plain': new Blob([plainText], {{ type: 'text/plain' }}),
      }});
      navigator.clipboard.write([clipboardItem]).then(() => {{
        const btn = document.querySelector('.copy-btn');
        btn.textContent = '✅ コピーしました';
        setTimeout(() => {{ btn.textContent = '📋 Slackにコピー'; }}, 2000);
      }});
    }}
  </script>
</body>
</html>"""
    return html


# ============================================================
# メイン
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="GitHub日報ジェネレーター")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="対象日 (YYYY-MM-DD形式、デフォルト: 今日)",
    )
    parser.add_argument(
        "--output-dir",
        default=os.path.expanduser("~/reports"),
        help="出力ディレクトリ (デフォルト: ~/reports)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="生成後にブラウザを開かない",
    )
    parser.add_argument(
        "--single-repo",
        action="store_true",
        help="従来の単一リポジトリ動作（カレントディレクトリのリポのみ対象）",
    )
    parser.add_argument(
        "--org",
        default=None,
        help="org名を明示指定（デフォルト: カレントリポのowner）",
    )
    parser.add_argument(
        "--backlog-space",
        default=os.environ.get("BACKLOG_SPACE", "e-ll.backlog.jp"),
        help="Backlogスペース (デフォルト: BACKLOG_SPACE環境変数 or e-ll.backlog.jp)",
    )
    parser.add_argument(
        "--backlog-api-key",
        default=os.environ.get("BACKLOG_API_KEY", ""),
        help="Backlog APIキー (デフォルト: BACKLOG_API_KEY環境変数)",
    )
    parser.add_argument(
        "--project",
        type=int,
        default=int(os.environ.get("GITHUB_PROJECT_NUMBER", "11")),
        help="GitHub Project番号 (デフォルト: GITHUB_PROJECT_NUMBER環境変数 or 11)",
    )
    parser.add_argument(
        "--no-project",
        action="store_true",
        help="Project Status セクションを無効化する",
    )
    parser.add_argument(
        "--all-projects",
        action="store_true",
        default=True,
        help="org内の全プロジェクトのアイテムを取得する (デフォルト: 有効)",
    )
    parser.add_argument(
        "--single-project",
        action="store_true",
        help="--project で指定した単一プロジェクトのみ取得する",
    )
    parser.add_argument(
        "--all-members",
        action="store_true",
        help="org全メンバー分のチーム日報を生成する",
    )
    args = parser.parse_args()
    if args.single_project:
        args.all_projects = False

    date_str = args.date
    print(f"📅 日報生成: {date_str}")

    # ユーザー情報取得
    username = get_username()
    if not username:
        print("❌ GitHubユーザー名を取得できません。gh auth login を実行してください。", file=sys.stderr)
        sys.exit(1)
    print(f"👤 ユーザー: {username}")

    # --all-members モード
    if args.all_members:
        org = args.org
        if not org:
            owner, _, _ = get_repo_info()
            org = owner
        if not org:
            print("❌ org名を特定できません。--org を指定してください。", file=sys.stderr)
            sys.exit(1)

        print(f"🏢 org: {org}")
        print("👥 チーム日報モード: 全メンバー")

        # メンバー一覧
        members = get_org_members(org)
        if not members:
            print("❌ orgメンバーを取得できません。", file=sys.stderr)
            sys.exit(1)
        print(f"  → {len(members)}名: {', '.join(members)}")

        # Project items (共通1回)
        print("\n📊 Project Status 取得中...")
        if args.all_projects:
            project_items, project_map = get_all_project_items(org)
        else:
            project_items, ptitle = get_project_items(org, args.project)
            project_map = {args.project: ptitle or f"Project #{args.project}"}
            for it in project_items:
                it["project_name"] = ptitle or f"Project #{args.project}"
        status_counts = {}
        if project_items:
            for it in project_items:
                if not it["is_pr"] and it["status"]:
                    status_counts[it["status"]] = status_counts.get(it["status"], 0) + 1
            print(f"  → ステータス分布: {status_counts}")
        else:
            print("  → Project データなし")
            project_items = []

        # メンバーごとにデータ収集
        import time
        members_data = []
        for mi, member in enumerate(members):
            print(f"\n👤 [{mi+1}/{len(members)}] {member}")

            # 活動のあるリポ検索 (Search API + Project items補完)
            print("  🔍 活動リポ検索...")
            repos = get_active_repos(org, member, date_str)
            # Project itemsのassigneeからリポを補完 (Search APIインデックス遅延対策)
            search_repo_set = {f"{r[0]}/{r[1]}" for r in repos}
            project_repos = set()
            for it in project_items:
                if member in it.get("assignees", []) and it.get("repo_full") and it["status"] in ("In progress", "In review"):
                    project_repos.add(it["repo_full"])
            supplemented = 0
            for full_name in sorted(project_repos - search_repo_set):
                parts = full_name.split("/", 1)
                if len(parts) == 2:
                    db = run_gh(["api", f"/repos/{full_name}", "--jq", ".default_branch"], allow_empty=True) or "main"
                    repos.append((parts[0], parts[1], db))
                    supplemented += 1
            if supplemented:
                print(f"    → {len(repos)}件 (Search: {len(repos) - supplemented}, Project補完: {supplemented})")
            else:
                print(f"    → {len(repos)}件")

            per_repo_data = {}
            for r_owner, r_repo, r_default_branch in repos:
                print(f"    📁 {r_owner}/{r_repo}")
                prs = get_prs(r_owner, r_repo, member, date_str)
                # PRのブランチもコミット検索対象に追加
                pr_branches = [pr["branch"] for pr in prs if pr.get("branch")]
                search_branches = [None] + pr_branches if pr_branches else [None]
                commits = get_commits(r_owner, r_repo, member, date_str, branches=search_branches)
                if commits and prs:
                    pr_commits, orphan_commits = match_commits_to_prs(commits, prs, r_owner, r_repo)
                elif commits:
                    pr_commits, orphan_commits = {}, commits
                else:
                    pr_commits, orphan_commits = {}, []
                review_comments = get_review_comments(r_owner, r_repo, member, date_str)
                reviewed_prs = get_pr_reviews(r_owner, r_repo, member, date_str)
                merged_prs = get_merged_by_user(r_owner, r_repo, member, date_str)
                pr_conversation, issue_comments = get_issue_comments(r_owner, r_repo, member, date_str)
                created_issues = get_created_issues(r_owner, r_repo, member, date_str)

                all_issue_numbers = set()
                for c in commits:
                    all_issue_numbers.update(c["issues"])
                for pr in prs:
                    all_issue_numbers.update(pr["issues"])
                issues = {}
                if all_issue_numbers:
                    issues = get_issue_details(r_owner, r_repo, all_issue_numbers)

                per_repo_data[r_repo] = {
                    "owner": r_owner, "repo": r_repo,
                    "prs": prs, "pr_commits": pr_commits,
                    "orphan_commits": orphan_commits,
                    "review_comments": review_comments,
                    "reviewed_prs": reviewed_prs,
                    "merged_prs": merged_prs,
                    "issues": issues,
                    "issue_comments": issue_comments,
                    "created_issues": created_issues,
                }

            # 本日活動Issue番号
            today_active_issues = set()
            for rd in per_repo_data.values():
                for pr in rd["prs"]:
                    for inum in pr.get("issues", []):
                        today_active_issues.add(int(inum))
                for ic in rd.get("issue_comments", []):
                    today_active_issues.add(ic["issue_number"])
                for ci in rd.get("created_issues", []):
                    today_active_issues.add(ci["number"])
                for pr in rd["prs"]:
                    today_active_issues.add(pr["number"])

            # レビュー依頼PR
            review_requested_prs = fetch_review_requested_prs(org, member)

            # Project summary (メンバー別)
            project_summary = build_project_summary(
                project_items, member, today_active_issues,
                review_requested_prs=review_requested_prs,
            )

            na_count = len(project_summary["next_actions"])
            print(f"  → タスク: {na_count}件, 活動リポ: {len(repos)}件, レビュー依頼: {len(review_requested_prs)}件")

            members_data.append({
                "username": member,
                "project_summary": project_summary,
                "per_repo_data": per_repo_data,
                "review_requested_prs": review_requested_prs,
            })

        # HTML生成
        print("\n📝 チーム日報HTML生成中...")
        html = generate_team_html(date_str, org, members_data, project_items, status_counts)

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"team-report-{date_str}.html"
        output_path.write_text(html, encoding="utf-8")
        print(f"✅ チーム日報生成完了: {output_path}")

        if not args.no_open:
            try:
                webbrowser.open(f"file://{output_path.resolve()}")
            except Exception:
                print(f"💡 手動で開いてください: {output_path}")

        return str(output_path)

    # リポジトリ / org 情報取得
    if args.single_repo:
        # 従来の単一リポ動作
        owner, repo, default_branch = get_repo_info()
        if not owner:
            print("❌ リポジトリ情報を取得できません。Gitリポジトリ内で実行してください。", file=sys.stderr)
            sys.exit(1)
        org = owner
        repos = [(owner, repo, default_branch)]
        print(f"📁 リポジトリ: {owner}/{repo}")

        # コミット検索対象ブランチを構築
        current_branch = get_current_branch()
        search_branches_map = {}
        branches = [None]
        if current_branch and current_branch != default_branch:
            branches.append(current_branch)
            print(f"🌿 ブランチ: {current_branch} (+ {default_branch})")
        else:
            print(f"🌿 ブランチ: {default_branch}")
        search_branches_map[repo] = branches
    else:
        # org全リポスキャン
        org = args.org
        if not org:
            owner, _, _ = get_repo_info()
            org = owner
        if not org:
            print("❌ org名を特定できません。--org を指定するか、Gitリポジトリ内で実行してください。", file=sys.stderr)
            sys.exit(1)
        print(f"🏢 org: {org}")
        print("🔍 活動のあるリポジトリを検索中...")
        repos = get_active_repos(org, username, date_str)
        if repos:
            print(f"  → {len(repos)}件のリポジトリで活動を検出:")
            for r_owner, r_repo, r_branch in repos:
                print(f"    📁 {r_owner}/{r_repo} (default: {r_branch})")
        else:
            print("  → 活動のあるリポジトリが見つかりませんでした")
        search_branches_map = {}
        for _, r_repo, _ in repos:
            search_branches_map[r_repo] = [None]

    # データ収集（リポジトリごと）
    print("🔍 データ収集中...")
    per_repo_data = {}
    all_tomorrow_plan = []

    for r_owner, r_repo, r_default_branch in repos:
        repo_label = f"{r_owner}/{r_repo}"
        print(f"\n  📁 {repo_label}")

        print("    - PR取得...")
        prs = get_prs(r_owner, r_repo, username, date_str)
        print(f"      → {len(prs)}件")

        print("    - コミット取得...")
        search_branches = search_branches_map.get(r_repo, [None])
        # PRのブランチもコミット検索対象に追加
        pr_branches = {pr["branch"] for pr in prs if pr.get("branch")}
        existing = set(search_branches) if search_branches else {None}
        for b in pr_branches:
            if b and b not in existing:
                search_branches.append(b)
        commits = get_commits(r_owner, r_repo, username, date_str, branches=search_branches)
        print(f"      → {len(commits)}件")

        # コミットをPRに紐付け
        if commits and prs:
            print("    - コミット⇔PR紐付け...")
            pr_commits, orphan_commits = match_commits_to_prs(commits, prs, r_owner, r_repo)
            matched = sum(len(cs) for cs in pr_commits.values())
            print(f"      → PR紐付き: {matched}件 / PR不在: {len(orphan_commits)}件")
        elif commits:
            pr_commits = {}
            orphan_commits = commits
            print(f"      → PR不在コミット: {len(orphan_commits)}件")
        else:
            pr_commits = {}
            orphan_commits = []

        print("    - レビューコメント取得...")
        review_comments = get_review_comments(r_owner, r_repo, username, date_str)
        comment_count = sum(len(v) for v in review_comments.values())
        print(f"      → インライン: {comment_count}件 ({len(review_comments)} PR)")

        print("    - Issue/PR会話コメント取得...")
        pr_conversation, issue_comments = get_issue_comments(r_owner, r_repo, username, date_str)
        print(f"      → PR会話: {sum(len(v) for v in pr_conversation.values())}件 ({len(pr_conversation)} PR)")
        print(f"      → Issueコメント: {len(issue_comments)}件")

        # PR会話コメントを review_comments にマージ（インラインと統合表示）
        for pr_num, conv_comments in pr_conversation.items():
            if pr_num in review_comments:
                review_comments[pr_num].extend(conv_comments)
            else:
                review_comments[pr_num] = conv_comments

        print("    - レビュー済みPR取得...")
        reviewed_prs = get_pr_reviews(r_owner, r_repo, username, date_str)
        print(f"      → {len(reviewed_prs)}件")

        print("    - マージしたPR取得...")
        merged_prs = get_merged_by_user(r_owner, r_repo, username, date_str)
        print(f"      → {len(merged_prs)}件")

        print("    - 作成Issue取得...")
        created_issues = get_created_issues(r_owner, r_repo, username, date_str)
        print(f"      → {len(created_issues)}件")

        # 関連Issue番号を収集
        all_issue_numbers = set()
        for c in commits:
            all_issue_numbers.update(c["issues"])
        for pr in prs:
            all_issue_numbers.update(pr["issues"])
        for ic in issue_comments:
            all_issue_numbers.add(str(ic["issue_number"]))
        for ci in created_issues:
            all_issue_numbers.add(str(ci["number"]))

        issues = {}
        if all_issue_numbers:
            print(f"    - 関連Issue取得 ({len(all_issue_numbers)}件)...")
            issues = get_issue_details(r_owner, r_repo, all_issue_numbers)
            print(f"      → {len(issues)}件取得")

        # 明日の予定推測（リポ別）
        print("    - 明日の予定推測...")
        repo_plan = get_tomorrow_plan(r_owner, r_repo, username, date_str, prs, review_comments)
        print(f"      → {len(repo_plan)}件")
        all_tomorrow_plan.extend(repo_plan)

        per_repo_data[r_repo] = {
            "owner": r_owner,
            "repo": r_repo,
            "prs": prs,
            "pr_commits": pr_commits,
            "orphan_commits": orphan_commits,
            "review_comments": review_comments,
            "reviewed_prs": reviewed_prs,
            "merged_prs": merged_prs,
            "issues": issues,
            "issue_comments": issue_comments,
            "created_issues": created_issues,
        }

    # Backlog活動取得（リポ横断）
    backlog_activities = []
    if args.backlog_api_key:
        print("\n  - Backlog活動取得...")
        bl_space = args.backlog_space
        bl_key = args.backlog_api_key
        bl_user = backlog_api(bl_space, "/users/myself", bl_key)
        if bl_user and isinstance(bl_user, dict) and "id" in bl_user:
            bl_user_id = bl_user["id"]
            backlog_activities = get_backlog_activities(bl_space, bl_key, bl_user_id, date_str)
            print(f"    → {len(backlog_activities)}件")
        else:
            print("    → Backlogユーザー情報取得失敗（スキップ）")

    # Project Status 取得
    project_summary = None
    if not args.no_project:
        print("\n  📊 Project Status 取得中...")
        if args.all_projects:
            project_items, project_map = get_all_project_items(org)
        else:
            project_items, ptitle = get_project_items(org, args.project)
            project_map = {args.project: ptitle or f"Project #{args.project}"}
            for it in project_items:
                it["project_name"] = ptitle or f"Project #{args.project}"
        if project_items:
            # 今日活動のあったIssue番号を全リポから収集
            today_active_issues = set()
            for rd in per_repo_data.values():
                for pr in rd["prs"]:
                    for inum in pr.get("issues", []):
                        today_active_issues.add(int(inum))
                for ic in rd.get("issue_comments", []):
                    today_active_issues.add(ic["issue_number"])
                for ci in rd.get("created_issues", []):
                    today_active_issues.add(ci["number"])
                # PR番号自体も追加（PRがProjectに紐づく場合）
                for pr in rd["prs"]:
                    today_active_issues.add(pr["number"])

            print("    - レビュー依頼PR取得...")
            review_requested_prs = fetch_review_requested_prs(org, username)
            print(f"      → {len(review_requested_prs)}件")

            project_summary = build_project_summary(
                project_items, username, today_active_issues,
                review_requested_prs=review_requested_prs,
            )
            ip_count = len(project_summary["in_progress"])
            ir_count = len(project_summary["in_review"])
            bl_count = len(project_summary["blocked"])
            rr_count = len(review_requested_prs)
            print(f"    → In Progress: {ip_count}, In Review: {ir_count}, Blocked: {bl_count}, Review Requested: {rr_count}")
            print(f"    → ステータス分布: {project_summary['status_counts']}")
        else:
            print("    → Project データ取得失敗またはアイテムなし（スキップ）")

    # HTML生成
    print("\n📝 HTML生成中...")
    html = generate_html(
        date_str, org, username,
        per_repo_data,
        backlog_activities=backlog_activities,
        tomorrow_plan=all_tomorrow_plan,
        single_repo=args.single_repo,
        project_summary=project_summary,
        project_items=project_items if project_items else [],
    )

    # 出力
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"daily-report-{date_str}.html"
    output_path.write_text(html, encoding="utf-8")
    print(f"✅ 日報生成完了: {output_path}")

    # Claude整形用 JSON出力
    json_data = {
        "date": date_str,
        "org": org,
        "username": username,
        "project_items": [],
        "activities": {},
    }
    if project_items:
        for it in project_items:
            json_data["project_items"].append({
                "number": it.get("number"),
                "title": it.get("title", ""),
                "url": it.get("url", ""),
                "state": it.get("state", ""),
                "status": it.get("status", ""),
                "repo": it.get("repo", ""),
                "repo_full": it.get("repo_full", ""),
                "assignees": it.get("assignees", []),
                "is_pr": it.get("is_pr", False),
                "is_parent": it.get("is_parent", False),
                "parent": it.get("parent"),
                "project_name": it.get("project_name", ""),
                "blocked": it.get("blocked", ""),
                "blocker_type": it.get("blocker_type", ""),
                "labels": it.get("labels", []),
            })
    for repo_name, rd in per_repo_data.items():
        repo_act = {}
        if rd.get("prs"):
            repo_act["prs"] = [
                {"number": p["number"], "title": p.get("title", ""), "state": p.get("state", ""),
                 "url": p.get("url", ""), "issues": p.get("issues", [])}
                for p in rd["prs"]
            ]
        if rd.get("issue_comments"):
            issues_data = rd.get("issues", {})
            repo_act["issue_comments"] = [
                {"issue_number": ic["issue_number"],
                 "issue_title": issues_data.get(ic["issue_number"], {}).get("title", ""),
                 "body_preview": ic.get("body", "")[:100], "url": ic.get("url", "")}
                for ic in rd["issue_comments"]
            ]
        if rd.get("created_issues"):
            repo_act["created_issues"] = [
                {"number": ci["number"], "title": ci.get("title", ""), "url": ci.get("url", "")}
                for ci in rd["created_issues"]
            ]
        if rd.get("review_comments"):
            repo_act["review_comments"] = [
                {"pr_number": rc.get("pr_number"), "pr_title": rc.get("pr_title", ""),
                 "url": rc.get("url", "")}
                for rc in rd["review_comments"]
            ]
        if rd.get("merged_prs"):
            repo_act["merged_prs"] = [
                {"number": mp["number"], "title": mp.get("title", ""), "url": mp.get("url", "")}
                for mp in rd["merged_prs"]
            ]
        if repo_act:
            json_data["activities"][repo_name] = repo_act
    json_path = output_dir / f"daily-report-{date_str}.json"
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📊 JSON出力: {json_path}")

    # ブラウザで開く
    if not args.no_open:
        try:
            webbrowser.open(f"file://{output_path.resolve()}")
            print("🌐 ブラウザで開きました")
        except Exception:
            print(f"💡 ブラウザで開けませんでした。手動で開いてください: {output_path}")

    return str(output_path)


if __name__ == "__main__":
    main()
