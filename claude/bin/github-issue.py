#!/usr/bin/env python3
"""GitHub Issue & Project操作CLI - Project運用方針に準拠した自動化ツール"""

import subprocess
import json
import sys
import os
import re
import argparse
import unicodedata
from datetime import datetime, date, timedelta
from typing import Optional, Dict, List, Any, Tuple

# ============================================================
# 定数
# ============================================================

DEFAULT_ORG = "e-llabo"
DEFAULT_PROJECT_NUMBER = 11
DEFAULT_REPO = "e-llabo/ELL_portal"

VALID_STATUSES = [
    "Backlog", "Ready", "In progress", "In review",
    "Approved", "Staging / Merged to Release", "Done",
]

VALID_BLOCKER_TYPES = [
    "Waiting External", "Waiting Other Task",
    "Need Decision", "Env/Infra", "Other",
]

VALID_TEMPLATES = [
    "task", "bug", "feature-parent",
    "feature-child-req", "feature-child-spec", "feature-child-impl",
]

# ============================================================
# gh CLI ヘルパー（daily-report.py と同パターン）
# ============================================================


def run_gh(args, allow_empty=False, timeout=60):
    """ghコマンドを実行してstdoutを返す"""
    result = subprocess.run(
        ["gh"] + args, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        if allow_empty:
            return ""
        print(f"Error: gh {' '.join(args[:6])}...\n{result.stderr.strip()}", file=sys.stderr)
        return ""
    return result.stdout.strip()


def run_gh_json(args, allow_empty=False, timeout=60):
    """ghコマンドを実行してJSONをパースして返す"""
    output = run_gh(args, allow_empty=allow_empty, timeout=timeout)
    if not output:
        return [] if allow_empty else None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return [] if allow_empty else None


def log(msg):
    print(f"[github-issue] {msg}")


def log_err(msg):
    print(f"[github-issue] Error: {msg}", file=sys.stderr)


# ============================================================
# 表示ユーティリティ
# ============================================================


def display_width(s):
    """CJK文字を考慮した表示幅を計算"""
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        w += 2 if eaw in ("F", "W", "A") else 1
    return w


def pad(s, width):
    """CJK対応で固定幅にパディング"""
    dw = display_width(s)
    return s + " " * max(0, width - dw)


def truncate(s, width):
    """CJK対応で固定幅に切り詰め"""
    w = 0
    result = []
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        cw = 2 if eaw in ("F", "W", "A") else 1
        if w + cw > width - 1:
            result.append("…")
            break
        result.append(ch)
        w += cw
    return "".join(result)


# ============================================================
# ProjectClient — GitHub ProjectV2 GraphQL APIクライアント
# ============================================================


class ProjectClient:
    """GitHub ProjectV2 の操作を集約するクライアント"""

    def __init__(self, org: str, project_number: int, repo: str):
        self.org = org
        self.project_number = project_number
        self.repo = repo
        self._project_id: Optional[str] = None
        self._fields: Optional[Dict] = None

    # --- プロジェクトメタデータ（遅延取得+キャッシュ） ---

    @property
    def project_id(self) -> str:
        if self._project_id is None:
            raw = run_gh([
                "project", "view", str(self.project_number),
                "--owner", self.org, "--format", "json",
            ])
            if not raw:
                log_err("Project取得に失敗しました")
                sys.exit(1)
            data = json.loads(raw)
            self._project_id = data["id"]
        return self._project_id

    @property
    def fields(self) -> Dict:
        """フィールド一覧を取得し {name: {id, options: {name: id}}} 形式で返す"""
        if self._fields is None:
            raw = run_gh([
                "project", "field-list", str(self.project_number),
                "--owner", self.org, "--format", "json",
            ])
            if not raw:
                log_err("フィールド一覧の取得に失敗しました")
                sys.exit(1)
            data = json.loads(raw)
            self._fields = {}
            for f in data.get("fields", []):
                name = f.get("name", "")
                entry = {"id": f.get("id", ""), "options": {}}
                for opt in f.get("options", []):
                    entry["options"][opt["name"]] = opt["id"]
                self._fields[name] = entry
        return self._fields

    def _get_field_id(self, field_name: str) -> str:
        fld = self.fields.get(field_name)
        if not fld:
            log_err(f"フィールド '{field_name}' が見つかりません")
            sys.exit(1)
        return fld["id"]

    def _get_option_id(self, field_name: str, option_name: str) -> str:
        fld = self.fields.get(field_name)
        if not fld:
            log_err(f"フィールド '{field_name}' が見つかりません")
            sys.exit(1)
        oid = fld["options"].get(option_name)
        if not oid:
            available = ", ".join(fld["options"].keys())
            log_err(f"'{field_name}' に '{option_name}' オプションがありません (利用可能: {available})")
            sys.exit(1)
        return oid

    # --- GraphQL クエリ ---

    def _graphql(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """GraphQL APIを実行して結果を返す"""
        cmd = ["api", "graphql", "-f", f"query={query}"]
        for k, v in (variables or {}).items():
            cmd += ["-F", f"{k}={v}"]
        raw = run_gh(cmd, timeout=120)
        if not raw:
            return {}
        return json.loads(raw)

    def get_all_items(self) -> List[Dict]:
        """全プロジェクトアイテムを取得（ページネーション付き）"""
        query_tmpl = """query($projectId:ID!{after_param}) {{
  node(id:$projectId) {{
    ... on ProjectV2 {{
      items(first:100{after_arg}) {{
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id
          content {{
            __typename
            ... on Issue {{
              number title state url
              parent {{ number }}
              assignees(first:5) {{ nodes {{ login }} }}
              milestone {{ title }}
            }}
            ... on PullRequest {{
              number title state url isDraft
            }}
          }}
          status: fieldValueByName(name:"Status") {{
            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
          }}
          blocked: fieldValueByName(name:"Blocked") {{
            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
          }}
          blockerType: fieldValueByName(name:"Blocker Type") {{
            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
          }}
          blockedSince: fieldValueByName(name:"Blocked Since") {{
            ... on ProjectV2ItemFieldDateValue {{ date }}
          }}
          targetDate: fieldValueByName(name:"Target date") {{
            ... on ProjectV2ItemFieldDateValue {{ date }}
          }}
        }}
      }}
    }}
  }}
}}"""
        all_nodes = []
        after = None

        while True:
            if after:
                q = query_tmpl.format(after_param=", $after:String", after_arg=", after:$after")
                data = self._graphql(q, {"projectId": self.project_id, "after": after})
            else:
                q = query_tmpl.format(after_param="", after_arg="")
                data = self._graphql(q, {"projectId": self.project_id})

            items_data = (data.get("data", {}).get("node", {}) or {}).get("items", {})
            nodes = items_data.get("nodes", [])
            all_nodes.extend(nodes)

            page_info = items_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            after = page_info.get("endCursor", "")

        return [self._parse_item(n) for n in all_nodes if n]

    def _parse_item(self, node: Dict) -> Dict:
        """GraphQLノードを平坦な辞書に変換"""
        content = node.get("content") or {}
        typename = content.get("__typename", "")

        assignees = []
        if "assignees" in content:
            assignees = [a["login"] for a in content.get("assignees", {}).get("nodes", [])]

        milestone = None
        if "milestone" in content and content["milestone"]:
            milestone = content["milestone"].get("title")

        return {
            "item_id": node.get("id", ""),
            "type": typename,
            "number": content.get("number"),
            "title": content.get("title", ""),
            "state": content.get("state", ""),
            "url": content.get("url", ""),
            "parent_number": (content.get("parent") or {}).get("number"),
            "assignees": assignees,
            "milestone": milestone,
            "is_draft": content.get("isDraft", False),
            "status": (node.get("status") or {}).get("name"),
            "blocked": (node.get("blocked") or {}).get("name"),
            "blocker_type": (node.get("blockerType") or {}).get("name"),
            "blocked_since": (node.get("blockedSince") or {}).get("date"),
            "target_date": (node.get("targetDate") or {}).get("date"),
        }

    def get_item_by_issue_number(self, issue_number: int) -> Optional[Dict]:
        """Issue番号でプロジェクトアイテムを検索"""
        items = self.get_all_items()
        for item in items:
            if item["type"] == "Issue" and item["number"] == issue_number:
                return item
        return None

    def get_issue_details(self, issue_number: int) -> Dict:
        """Issue詳細をREST APIで取得"""
        data = run_gh_json([
            "issue", "view", str(issue_number),
            "-R", self.repo,
            "--json", "number,title,body,state,labels,assignees,milestone,url",
        ], allow_empty=True)
        return data or {}

    def get_issue_linked_prs(self, issue_number: int) -> List[Dict]:
        """Issueにリンクされたを取得"""
        owner, repo_name = self.repo.split("/")
        query = """query($owner:String!, $repo:String!, $number:Int!) {
  repository(owner:$owner, name:$repo) {
    issue(number:$number) {
      timelineItems(first:50, itemTypes:[CONNECTED_EVENT, CROSS_REFERENCED_EVENT]) {
        nodes {
          __typename
          ... on ConnectedEvent {
            subject { ... on PullRequest { number title state isDraft url } }
          }
          ... on CrossReferencedEvent {
            source { ... on PullRequest { number title state isDraft url } }
          }
        }
      }
    }
  }
}"""
        data = self._graphql(query, {"owner": owner, "repo": repo_name, "number": str(issue_number)})
        prs = []
        seen = set()
        nodes = (((data.get("data", {}).get("repository") or {}).get("issue") or {})
                 .get("timelineItems", {}).get("nodes", []))
        for node in nodes:
            pr = node.get("subject") or node.get("source") or {}
            if pr.get("number") and pr["number"] not in seen:
                seen.add(pr["number"])
                prs.append(pr)
        return prs

    # --- ミューテーション ---

    def set_single_select(self, item_id: str, field_name: str, option_name: str):
        """単一選択フィールドを設定"""
        field_id = self._get_field_id(field_name)
        option_id = self._get_option_id(field_name, option_name)
        query = """mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $optionId:String!) {
  updateProjectV2ItemFieldValue(input:{
    projectId:$projectId, itemId:$itemId, fieldId:$fieldId,
    value:{ singleSelectOptionId:$optionId }
  }) { projectV2Item { id } }
}"""
        self._graphql(query, {
            "projectId": self.project_id, "itemId": item_id,
            "fieldId": field_id, "optionId": option_id,
        })

    def set_date_field(self, item_id: str, field_name: str, date_str: str):
        """日付フィールドを設定（YYYY-MM-DD形式）"""
        field_id = self._get_field_id(field_name)
        query = """mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!, $dateValue:Date!) {
  updateProjectV2ItemFieldValue(input:{
    projectId:$projectId, itemId:$itemId, fieldId:$fieldId,
    value:{ date:$dateValue }
  }) { projectV2Item { id } }
}"""
        self._graphql(query, {
            "projectId": self.project_id, "itemId": item_id,
            "fieldId": field_id, "dateValue": date_str,
        })

    def clear_field(self, item_id: str, field_name: str):
        """フィールド値をクリア"""
        field_id = self._get_field_id(field_name)
        query = """mutation($projectId:ID!, $itemId:ID!, $fieldId:ID!) {
  clearProjectV2ItemFieldValue(input:{
    projectId:$projectId, itemId:$itemId, fieldId:$fieldId
  }) { projectV2Item { id } }
}"""
        self._graphql(query, {
            "projectId": self.project_id, "itemId": item_id, "fieldId": field_id,
        })

    def set_status(self, item_id: str, status_name: str):
        self.set_single_select(item_id, "Status", status_name)

    def set_blocked(self, item_id: str, value: str):
        """Blocked フィールドを Yes/No に設定"""
        self.set_single_select(item_id, "Blocked", value)

    def set_blocker_type(self, item_id: str, blocker_type: str):
        self.set_single_select(item_id, "Blocker Type", blocker_type)

    def set_blocked_since(self, item_id: str, date_str: str):
        self.set_date_field(item_id, "Blocked Since", date_str)

    def set_target_date(self, item_id: str, date_str: str):
        self.set_date_field(item_id, "Target date", date_str)

    def clear_blocked_fields(self, item_id: str):
        """Blocked関連フィールドをすべてクリア"""
        self.set_blocked(item_id, "No")
        for field_name in ["Blocker Type", "Blocked Since"]:
            if field_name in self.fields:
                try:
                    self.clear_field(item_id, field_name)
                except Exception:
                    pass

    def add_issue_to_project(self, issue_node_id: str) -> Optional[str]:
        """IssueをProjectに追加し、item_idを返す"""
        query = """mutation($projectId:ID!, $contentId:ID!) {
  addProjectV2ItemById(input:{ projectId:$projectId, contentId:$contentId }) {
    item { id }
  }
}"""
        data = self._graphql(query, {
            "projectId": self.project_id, "contentId": issue_node_id,
        })
        return ((data.get("data", {}).get("addProjectV2ItemById") or {}).get("item") or {}).get("id")

    def add_issue_comment(self, issue_number: int, body: str):
        """Issueにコメントを追加"""
        run_gh([
            "issue", "comment", str(issue_number),
            "-R", self.repo, "--body", body,
        ])


# ============================================================
# ゲートチェック
# ============================================================


def _extract_depends_on(body: str) -> List[int]:
    """Issue本文から Depends on #X 形式の依存Issue番号を抽出"""
    dep_section = ""
    if "依存関係" in body:
        parts = body.split("依存関係", 1)
        if len(parts) > 1:
            dep_section = parts[1].split("##")[0]
    elif "Depends on" in body:
        dep_section = body
    numbers = re.findall(r"#(\d+)", dep_section)
    return [int(n) for n in numbers]


def validate_ready_gate(client: ProjectClient, issue_number: int, item: Dict) -> Tuple[bool, List[str]]:
    """Backlog -> Ready ゲート（§7準拠）"""
    errors = []
    if not item.get("milestone"):
        errors.append("Milestone が未設定です")
    if not item.get("target_date"):
        errors.append("Target date が未設定です")
    issue = client.get_issue_details(issue_number)
    body = issue.get("body") or ""
    if "完了条件" not in body and "受け入れ条件" not in body:
        errors.append("完了条件（受け入れ条件）セクションが見つかりません")
    # 依存関係が明示済みか（セクション存在 + 空欄でないか）
    if "依存関係" in body:
        dep_section = body.split("依存関係", 1)[1].split("##")[0]
        lines = [l.strip() for l in dep_section.strip().splitlines() if l.strip() and not l.strip().startswith("<!--")]
        all_empty = all(re.match(r"^-\s*(関連Issue|関連PR|外部依存)\s*:\s*$", l) for l in lines)
        if all_empty:
            errors.append("依存関係が未記載です（なしの場合は「なし」と明記してください）")
    else:
        errors.append("依存関係セクションが見つかりません")
    return (len(errors) == 0, errors)


def validate_in_progress_gate(client: ProjectClient, issue_number: int, item: Dict) -> Tuple[bool, List[str]]:
    """Ready -> In progress ゲート（§9準拠）"""
    errors = []
    # 1. 関連PR（Draft可）が存在
    prs = client.get_issue_linked_prs(issue_number)
    if not prs:
        errors.append("関連PRが未作成です（Draft PRを作成してください。--with-pr で自動作成も可能）")
    else:
        # 2. PR本文に実装方針・影響範囲が記載されているか
        for pr in prs:
            pr_detail = run_gh_json([
                "pr", "view", str(pr["number"]), "-R", client.repo,
                "--json", "number,body",
            ], allow_empty=True)
            if pr_detail:
                pr_body = pr_detail.get("body") or ""
                if "実装方針" not in pr_body and "影響範囲" not in pr_body:
                    errors.append(f"PR #{pr['number']} の本文に実装方針・影響範囲が未記載です")
                break  # 先頭PRのみチェック
    # 3. 着手時の作業分解コメント
    comments = run_gh_json([
        "api", f"/repos/{client.repo}/issues/{issue_number}/comments",
        "--jq", "[.[] | {body: .body, created_at: .created_at}]",
    ], allow_empty=True) or []
    if not comments:
        errors.append("Issueコメントがありません（着手時の作業分解を宣言してください）")
    # 4. Blocked整合性チェック
    if item.get("blocked") == "Yes" and not item.get("blocker_type"):
        errors.append("Blocked=Yes ですが Blocker Type が未設定です")
    return (len(errors) == 0, errors)


def validate_in_review_gate(client: ProjectClient, issue_number: int, item: Dict) -> Tuple[bool, List[str]]:
    """In progress -> In review ゲート（§8準拠）"""
    errors = []
    prs = client.get_issue_linked_prs(issue_number)
    if not prs:
        errors.append("関連PRがありません")
    else:
        non_draft = [p for p in prs if not p.get("isDraft", True)]
        if not non_draft:
            errors.append("関連PRがすべてDraftです（Draft解除してください）")
    # Target date現実性チェック
    if item.get("target_date"):
        try:
            target = datetime.strptime(item["target_date"], "%Y-%m-%d").date()
            if target < date.today():
                errors.append(f"Target date ({item['target_date']}) が過去日です。更新を検討してください")
        except ValueError:
            pass
    # 依存Issueの状態確認
    issue = client.get_issue_details(issue_number)
    body = issue.get("body") or ""
    dep_numbers = _extract_depends_on(body)
    for dep_num in dep_numbers:
        dep_detail = run_gh_json([
            "issue", "view", str(dep_num), "-R", client.repo,
            "--json", "number,state,title",
        ], allow_empty=True)
        if dep_detail and dep_detail.get("state") == "OPEN":
            dep_item = client.get_item_by_issue_number(dep_num)
            dep_status = (dep_item.get("status") or "") if dep_item else ""
            if dep_status not in ("Approved", "Staging / Merged to Release", "Done"):
                errors.append(f"依存Issue #{dep_num} がまだ {dep_status or 'OPEN'} です")
    return (len(errors) == 0, errors)


GATE_MAP = {
    "Ready": validate_ready_gate,
    "In progress": validate_in_progress_gate,
    "In review": validate_in_review_gate,
}


# ============================================================
# 親子課題同期ロジック（sync_project_parent_status.sh のPython移植）
# ============================================================


def compute_parent_target(child_statuses: List[Optional[str]]) -> str:
    """子課題のステータス集合から親のターゲットステータスを算出"""
    statuses = [s if s else "Backlog" for s in child_statuses]

    if "In progress" in statuses:
        return "In progress"
    if all(s == "Done" for s in statuses):
        return "Done"
    if "In review" in statuses:
        return "In review"
    if any(s in ("Approved", "Staging / Merged to Release") for s in statuses):
        return "Approved"
    return "Ready"


# ============================================================
# Issue本文テンプレート生成
# ============================================================


def _common_blocked_section():
    return (
        "## Blocked情報（該当時のみ）\n\n"
        "- Blocked: `No` / `Yes`\n"
        "- Blocker Type: `Waiting External` / `Waiting Other Task` / `Need Decision` / `Env/Infra` / `Other`\n"
        "- 解除条件: \n"
    )


def _common_deps_section(depends_on=None):
    deps = ", ".join(f"#{n}" for n in depends_on) if depends_on else ""
    return f"## 依存関係\n\n- 関連Issue: {deps}\n- 関連PR: \n- 外部依存: \n"


def build_issue_body_task(estimate=None, depends_on=None):
    """task.md テンプレート準拠"""
    est_line = f"- 見積り: `est:{estimate}`" if estimate else "- 見積り（任意, 例: `est:0.5d` / `est:2d`）: "
    return "\n".join([
        "## 概要\n\n<!-- 何を、なぜやるかを1-3行で記載 -->\n",
        "## 完了条件（受け入れ条件）\n\n- [ ] \n",
        "## スコープ\n\n- 対象: \n- 対象外: \n",
        f"## 期日・見積り\n\n- Target date（必須）: \n{est_line}\n"
        "- Milestone補足: Milestoneの期日（Due date）は未定でも可（スコープ管理を優先）\n",
        _common_deps_section(depends_on),
        _common_blocked_section(),
        "## 補足\n\n<!-- ログ、リンク、調査メモなど -->\n",
        "## 起票後チェック（Project項目）\n\n"
        "- [ ] Milestone を設定した\n"
        "- [ ] Target date を設定した\n"
        "- [ ] 初期ステータスを `Backlog` または `Ready` に設定した\n"
        "- [ ] `Blocked=Yes` の場合、`Blocker Type` と `Blocked Since` を設定した\n",
    ])


def build_issue_body_bug():
    """bug-light.md テンプレート準拠"""
    return "\n".join([
        "## 概要\n\n<!-- 何が起きているかを1-3行で記載 -->\n",
        "## 期待動作 / 実際の動作\n\n- 期待: \n- 実際: \n",
        "## 再現情報（最小）\n\n"
        "- 環境: `dev` / `stg` / `prod` / `local`\n"
        "- 発生箇所（画面/API/バッチ）: \n"
        "- 再現手順（分かる範囲で可）:\n1.\n2.\n3.\n",
        "## 影響と緊急度（一次判定）\n\n"
        "- 影響範囲: `単一ユーザー` / `一部ユーザー` / `全体`\n"
        "- 緊急度（暫定）: `低` / `中` / `高`\n"
        "- 回避策: `あり` / `なし`\n",
        "## 完了条件（受け入れ条件）\n\n"
        "- [ ] 原因が特定されている\n"
        "- [ ] 修正が反映され、再発しないことを確認できる\n",
        "## 補足\n\n<!-- 画像、ログ、関連リンクなど -->\n",
        "## 起票後チェック（軽量バグ向け）\n\n"
        "- [ ] 初期ステータスを `Backlog` に設定した\n"
        "- [ ] 重複候補を確認した（あればリンクを追記）\n"
        "- [ ] `Ready` に上げるタイミングで `Milestone` / `Target date` / `Priority` を設定する\n",
    ])


def build_issue_body_feature_parent(depends_on=None):
    """feature-parent テンプレート（1機能を横断管理する親Issue）"""
    return "\n".join([
        "## 機能概要\n\n<!-- この機能が何を実現するかを1-3行で記載 -->\n",
        "## ゴール（受け入れ条件）\n\n- [ ] \n",
        "## スコープ\n\n- 対象: \n- 対象外: \n",
        "## 子課題\n\n"
        "- [ ] 要件調整: \n"
        "- [ ] 仕様書作成: \n"
        "- [ ] 実装（テスト込み）: \n",
        _common_deps_section(depends_on),
        "## 期日・見積り\n\n- Target date: \n- 見積り合計: \n",
        "## 補足\n\n<!-- 背景、制約、関連資料など -->\n",
        "## 起票後チェック\n\n"
        "- [ ] Milestone を設定した\n"
        "- [ ] 子Issueを作成しProjectに追加した\n"
        "- [ ] 初期ステータスを `Backlog` に設定した\n",
    ])


def build_issue_body_feature_child(phase, depends_on=None):
    """feature-child テンプレート（要件/仕様/実装の子Issue）"""
    phase_map = {
        "req": ("要件調整", "要件が確定し、関係者の合意が得られている"),
        "spec": ("仕様書作成", "仕様書（docs PR）がレビュー済みである"),
        "impl": ("実装（テスト込み）", "実装PRがマージされ、テストが通過している"),
    }
    phase_name, default_ac = phase_map.get(phase, ("作業", "完了条件を記載"))

    return "\n".join([
        f"## 概要\n\n<!-- {phase_name}として何をするかを記載 -->\n",
        f"## 完了条件（受け入れ条件）\n\n- [ ] {default_ac}\n",
        "## スコープ\n\n- 対象: \n- 対象外: \n",
        _common_deps_section(depends_on),
        _common_blocked_section(),
        "## 成果物\n\n- [ ] docs PR: \n- [ ] 実装 PR: \n",
        "## 補足\n\n<!-- ログ、リンク、調査メモなど -->\n",
        "## 起票後チェック（Project項目）\n\n"
        "- [ ] 親Issueに紐づけた\n"
        "- [ ] Milestone を設定した\n"
        "- [ ] Target date を設定した\n"
        "- [ ] 初期ステータスを `Backlog` または `Ready` に設定した\n",
    ])


def build_issue_body(template="task", estimate=None, depends_on=None):
    """テンプレート種別に応じてIssue本文を構築"""
    if template == "bug":
        return build_issue_body_bug()
    elif template == "feature-parent":
        return build_issue_body_feature_parent(depends_on=depends_on)
    elif template in ("feature-child-req", "feature-child-spec", "feature-child-impl"):
        phase = template.split("-")[-1]  # req / spec / impl
        return build_issue_body_feature_child(phase, depends_on=depends_on)
    else:  # task (default)
        return build_issue_body_task(estimate=estimate, depends_on=depends_on)


# ============================================================
# サブコマンド実装
# ============================================================


def cmd_create(args, client: ProjectClient) -> int:
    """Issue起票 + Project追加 + フィールド設定"""
    template = getattr(args, "template", "task")

    # Ready化時のゲートチェック（Milestone/Target date必須）— bug は Backlog 時免除
    if args.status == "Ready":
        if not args.milestone:
            log_err("--status Ready には --milestone が必須です")
            return 1
        if not args.target_date:
            log_err("--status Ready には --target-date が必須です")
            return 1

    # 依存先パース
    depends_on = []
    if args.depends_on:
        depends_on = [n.strip().lstrip("#") for n in args.depends_on.split(",")]

    # Issue本文
    body = args.body
    if not body:
        body = build_issue_body(template=template, estimate=args.estimate, depends_on=depends_on)

    # ラベル構築（bug テンプレートは自動で bug ラベル付与）
    labels = []
    if args.labels:
        labels = [l.strip() for l in args.labels.split(",")]
    if template == "bug" and "bug" not in labels:
        labels.append("bug")

    # タイトルプレフィクス（テンプレートに応じた自動付与）
    title = args.title
    prefix_map = {
        "bug": "[BUG] ",
        "feature-parent": "[FEATURE] ",
    }
    prefix = prefix_map.get(template, "")
    if prefix and not title.startswith(prefix):
        title = prefix + title

    # gh issue create
    create_cmd = [
        "issue", "create", "-R", client.repo,
        "--title", title, "--body", body,
    ]
    if args.milestone:
        create_cmd += ["--milestone", args.milestone]
    for label in labels:
        create_cmd += ["--label", label]
    if args.assignee:
        create_cmd += ["--assignee", args.assignee]

    result = run_gh(create_cmd)
    if not result:
        log_err("Issue作成に失敗しました")
        return 1

    # URLからIssue番号抽出
    issue_url = result.strip()
    m = re.search(r"/issues/(\d+)", issue_url)
    if not m:
        log_err(f"Issue番号の抽出に失敗: {issue_url}")
        return 1
    issue_number = int(m.group(1))
    log(f"Issue #{issue_number} を作成しました: {issue_url}")

    # node_id 取得
    node_id = run_gh([
        "api", f"/repos/{client.repo}/issues/{issue_number}",
        "--jq", ".node_id",
    ])
    if not node_id:
        log_err("Issue node_id の取得に失敗しました")
        return 1

    # Projectに追加
    item_id = client.add_issue_to_project(node_id)
    if not item_id:
        log_err("Projectへの追加に失敗しました")
        return 1
    log(f"Project #{client.project_number} に追加しました")

    # ステータス設定
    client.set_status(item_id, args.status)
    log(f"Status = {args.status}")

    # Target date 設定
    if args.target_date:
        client.set_target_date(item_id, args.target_date)
        log(f"Target date = {args.target_date}")

    # 親課題設定
    if args.parent:
        parent_node_id = run_gh([
            "api", f"/repos/{client.repo}/issues/{args.parent}",
            "--jq", ".node_id",
        ])
        if parent_node_id:
            query = """mutation($parentId:ID!, $childId:ID!) {
  addSubIssue(input:{ issueId:$parentId, subIssueId:$childId }) {
    issue { id number }
    subIssue { id number }
  }
}"""
            client._graphql(query, {"parentId": parent_node_id, "childId": node_id})
            log(f"親課題 #{args.parent} に紐付けました")

    log(f"完了: {issue_url}")
    return 0


def cmd_status(args, client: ProjectClient) -> int:
    """ステータス変更（ゲートチェック付き）"""
    item = client.get_item_by_issue_number(args.issue_number)
    if not item:
        log_err(f"Issue #{args.issue_number} はProject内に見つかりません")
        return 1

    current = item.get("status") or "(なし)"
    target = args.new_status

    if current == target:
        log(f"Issue #{args.issue_number}: 既に {target} です")
        return 0

    # ゲートチェック
    if not args.force and target in GATE_MAP:
        passed, messages = GATE_MAP[target](client, args.issue_number, item)
        if not passed:
            print(f"Gate check failed for '{target}':")
            for msg in messages:
                print(f"  - {msg}")
            print("Use --force to skip gate validation")
            return 1

    # ステータス変更実行
    client.set_status(item["item_id"], target)
    log(f"Issue #{args.issue_number}: {current} -> {target}")

    # --with-pr: In progress移行時にDraft PR自動作成
    if args.with_pr and target == "In progress":
        issue = client.get_issue_details(args.issue_number)
        title = issue.get("title", f"Issue #{args.issue_number}")
        # まず新しいブランチを作成
        branch_name = f"feature/issue-{args.issue_number}"
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", branch_name],
            capture_output=True, text=True,
        )
        pr_result = run_gh([
            "pr", "create", "--draft",
            "--title", f"WIP: {title}",
            "--body", f"Closes #{args.issue_number}\n\n## 実装方針・影響範囲\n\n- 実装方針: \n- 影響範囲: \n",
            "-R", client.repo,
        ])
        if pr_result:
            log(f"Draft PR作成: {pr_result}")

    return 0


def cmd_list(args, client: ProjectClient) -> int:
    """Issue一覧表示（Project情報付き）"""
    items = client.get_all_items()

    # Issueのみフィルタ
    items = [i for i in items if i["type"] == "Issue"]

    # フィルタ適用
    if args.status:
        items = [i for i in items if i.get("status") == args.status]
    if args.blocked:
        items = [i for i in items if i.get("blocked") == "Yes"]
    if args.assignee:
        items = [i for i in items if args.assignee in i.get("assignees", [])]
    if args.parent:
        items = [i for i in items if i.get("parent_number") == args.parent]

    # stateフィルタ（デフォルトはOPENのみ、--allで全件）
    if not args.show_all:
        items = [i for i in items if i.get("state") == "OPEN"]

    if not items:
        log("該当するIssueはありません")
        return 0

    # ソート: ステータス順 -> 番号順
    status_order = {s: i for i, s in enumerate(VALID_STATUSES)}
    items.sort(key=lambda x: (status_order.get(x.get("status") or "Backlog", 99), x.get("number", 0)))

    # テーブル表示
    col_w = {"num": 5, "title": 36, "status": 28, "blocked": 10, "assignee": 12, "target": 12, "parent": 7}

    header = (
        f"{'#':<{col_w['num']}} "
        f"{pad('Title', col_w['title'])} "
        f"{pad('Status', col_w['status'])} "
        f"{pad('Blocked', col_w['blocked'])} "
        f"{pad('Assignee', col_w['assignee'])} "
        f"{pad('Target', col_w['target'])} "
        f"{'Parent':<{col_w['parent']}}"
    )
    sep = "-" * len(header)
    print(header)
    print(sep)

    for item in items:
        num = str(item.get("number", ""))
        title = truncate(item.get("title", ""), col_w["title"])
        status = item.get("status") or ""
        blocked_val = item.get("blocked") or "No"
        if blocked_val == "Yes" and item.get("blocker_type"):
            bt_short = item["blocker_type"][:3]
            blocked_val = f"Yes({bt_short})"
        assignees = ",".join(item.get("assignees", []))[:col_w["assignee"]]
        target = item.get("target_date") or ""
        parent = f"#{item['parent_number']}" if item.get("parent_number") else ""

        print(
            f"{num:<{col_w['num']}} "
            f"{pad(title, col_w['title'])} "
            f"{pad(status, col_w['status'])} "
            f"{pad(blocked_val, col_w['blocked'])} "
            f"{pad(assignees, col_w['assignee'])} "
            f"{pad(target, col_w['target'])} "
            f"{parent}"
        )

    print(f"\n合計: {len(items)}件")
    return 0


def cmd_block(args, client: ProjectClient) -> int:
    """ブロック設定"""
    item = client.get_item_by_issue_number(args.issue_number)
    if not item:
        log_err(f"Issue #{args.issue_number} はProject内に見つかりません")
        return 1

    client.set_blocked(item["item_id"], "Yes")
    client.set_blocker_type(item["item_id"], args.blocker_type)
    client.set_blocked_since(item["item_id"], date.today().isoformat())
    log(f"Issue #{args.issue_number}: Blocked=Yes, Type={args.blocker_type}, Since={date.today().isoformat()}")

    if args.reason:
        comment = (
            f"**Blocked**: {args.blocker_type}\n"
            f"**理由**: {args.reason}\n"
            f"**解除条件**: (要記入)"
        )
        client.add_issue_comment(args.issue_number, comment)
        log("コメントを追加しました")

    return 0


def cmd_unblock(args, client: ProjectClient) -> int:
    """ブロック解除"""
    item = client.get_item_by_issue_number(args.issue_number)
    if not item:
        log_err(f"Issue #{args.issue_number} はProject内に見つかりません")
        return 1

    client.clear_blocked_fields(item["item_id"])
    log(f"Issue #{args.issue_number}: ブロック解除しました")

    if args.comment:
        comment = f"**Unblocked**: {args.comment}"
        client.add_issue_comment(args.issue_number, comment)
        log("コメントを追加しました")

    return 0


def cmd_sync_parents(args, client: ProjectClient) -> int:
    """親子課題ステータス同期"""
    items = client.get_all_items()
    issues = [i for i in items if i["type"] == "Issue"]

    # 親 -> 子ステータス/子state のマッピング構築
    item_by_number = {}
    child_statuses_by_parent = {}
    child_states_by_parent = {}

    for issue in issues:
        num = issue["number"]
        if num is None:
            continue
        item_by_number[num] = issue
        parent = issue.get("parent_number")
        if parent:
            child_statuses_by_parent.setdefault(parent, []).append(issue.get("status"))
            child_states_by_parent.setdefault(parent, []).append(issue.get("state"))

    status_updates = 0
    close_updates = 0
    checked = 0

    for parent_num, child_statuses in child_statuses_by_parent.items():
        parent_item = item_by_number.get(parent_num)
        if not parent_item:
            continue

        checked += 1
        target = compute_parent_target(child_statuses)
        current = parent_item.get("status") or ""

        # ステータス更新
        if current != target:
            if args.dry_run:
                log(f"DRY-RUN: #{parent_num} {current} -> {target}")
            else:
                client.set_status(parent_item["item_id"], target)
                log(f"#{parent_num}: {current} -> {target}")
            status_updates += 1

        # 全子課題CLOSEDなら親もclose
        child_states = child_states_by_parent.get(parent_num, [])
        all_closed = all(s == "CLOSED" for s in child_states)
        if parent_item.get("state") == "OPEN" and all_closed:
            if args.dry_run:
                log(f"DRY-RUN: close #{parent_num}")
            else:
                run_gh(["issue", "close", str(parent_num), "-R", client.repo])
                log(f"close #{parent_num}")
            close_updates += 1

    log(f"checked={checked} status_updates={status_updates} close_updates={close_updates}")
    return 0


def cmd_weekly_check(args, client: ProjectClient) -> int:
    """週次メンテナンスチェック"""
    items = client.get_all_items()
    issues = [i for i in items if i["type"] == "Issue"]

    print("=== 週次メンテナンスチェック ===\n")

    # 1. Done/Closed 不整合
    mismatches = []
    for issue in issues:
        status = issue.get("status") or ""
        state = issue.get("state") or ""
        num = issue.get("number", "?")
        title = truncate(issue.get("title", ""), 30)
        if status == "Done" and state == "OPEN":
            mismatches.append(f"  #{num}: Status=Done but OPEN -> gh issue close {num} -R {client.repo}")
        elif state == "CLOSED" and status != "Done":
            mismatches.append(f"  #{num}: Status={status} but CLOSED -> Status=Done に更新推奨")

    print(f"## Done/Closed 不整合 ({len(mismatches)}件)")
    if mismatches:
        print("\n".join(mismatches))
    else:
        print("  なし")
    print()

    # 2. 長期ブロック (>3営業日)
    stale_blocked = []
    today = date.today()
    for issue in issues:
        if issue.get("blocked") != "Yes":
            continue
        since_str = issue.get("blocked_since")
        if since_str:
            try:
                since = datetime.strptime(since_str, "%Y-%m-%d").date()
                days = (today - since).days
                if days > 3:
                    num = issue.get("number", "?")
                    bt = issue.get("blocker_type") or "?"
                    stale_blocked.append(f"  #{num}: Blocked since {since_str} ({days}日) / Type={bt}")
            except ValueError:
                pass

    print(f"## 長期ブロック (>3営業日) ({len(stale_blocked)}件)")
    if stale_blocked:
        print("\n".join(stale_blocked))
    else:
        print("  なし")
    print()

    # 3. ステータス矛盾
    contradictions = []
    for issue in issues:
        if issue.get("state") != "OPEN":
            continue
        status = issue.get("status") or ""
        num = issue.get("number", "?")
        if status == "In review":
            prs = client.get_issue_linked_prs(num)
            if not prs:
                contradictions.append(f"  #{num}: Status=In review but 関連PRなし")
            else:
                non_draft = [p for p in prs if not p.get("isDraft", True)]
                if not non_draft:
                    contradictions.append(f"  #{num}: Status=In review but 全PRがDraft")

    print(f"## ステータス矛盾 ({len(contradictions)}件)")
    if contradictions:
        print("\n".join(contradictions))
    else:
        print("  なし")
    print()

    # 4. 親子課題同期候補
    item_by_number = {i["number"]: i for i in issues if i["number"]}
    child_statuses_by_parent = {}
    for issue in issues:
        parent = issue.get("parent_number")
        if parent:
            child_statuses_by_parent.setdefault(parent, []).append(issue.get("status"))

    sync_candidates = []
    for parent_num, child_statuses in child_statuses_by_parent.items():
        parent_item = item_by_number.get(parent_num)
        if not parent_item:
            continue
        target = compute_parent_target(child_statuses)
        current = parent_item.get("status") or ""
        if current != target:
            sync_candidates.append(f"  #{parent_num}: current={current}, children suggest={target}")

    print(f"## 親子課題同期候補 ({len(sync_candidates)}件)")
    if sync_candidates:
        print("\n".join(sync_candidates))
    else:
        print("  なし")
    print()

    # 5. Backlog棚卸し候補（Ready化可能なBacklogアイテム）
    ready_candidates = []
    for issue in issues:
        if issue.get("state") != "OPEN":
            continue
        if (issue.get("status") or "") != "Backlog":
            continue
        has_milestone = bool(issue.get("milestone"))
        has_target = bool(issue.get("target_date"))
        num = issue.get("number", "?")
        title = truncate(issue.get("title", ""), 30)
        if has_milestone and has_target:
            ready_candidates.append(f"  #{num}: {title} (Milestone/Target date設定済 -> Ready化検討)")
        elif has_milestone or has_target:
            missing = []
            if not has_milestone:
                missing.append("Milestone")
            if not has_target:
                missing.append("Target date")
            ready_candidates.append(f"  #{num}: {title} (不足: {', '.join(missing)})")

    print(f"## Backlog棚卸し候補 ({len(ready_candidates)}件)")
    if ready_candidates:
        print("\n".join(ready_candidates))
    else:
        print("  なし")
    print()

    total = len(mismatches) + len(stale_blocked) + len(contradictions) + len(sync_candidates) + len(ready_candidates)
    if total == 0:
        print("問題は検出されませんでした。")
    else:
        print(f"合計 {total}件 の要確認項目があります。")

    return 0


# ============================================================
# メイン
# ============================================================


def main():
    parser = argparse.ArgumentParser(
        description="GitHub Issue & Project操作CLI — Project運用方針準拠",
    )
    parser.add_argument("--org", default=DEFAULT_ORG, help=f"GitHub org名 (デフォルト: {DEFAULT_ORG})")
    parser.add_argument("--project", type=int, default=DEFAULT_PROJECT_NUMBER,
                        help=f"Project番号 (デフォルト: {DEFAULT_PROJECT_NUMBER})")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"リポジトリ (デフォルト: {DEFAULT_REPO})")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- create ---
    p_create = subparsers.add_parser("create", help="Issue起票")
    p_create.add_argument("--title", required=True, help="Issueタイトル")
    p_create.add_argument("--template", default="task", choices=VALID_TEMPLATES,
                          help="テンプレート種別 (デフォルト: task)")
    p_create.add_argument("--body", default=None, help="Issue本文（省略時はテンプレートから生成）")
    p_create.add_argument("--milestone", default=None, help="マイルストーン名")
    p_create.add_argument("--target-date", default=None, help="期日 (YYYY-MM-DD)")
    p_create.add_argument("--estimate", default=None, help="見積り (例: 0.5d, 2d)")
    p_create.add_argument("--status", default="Backlog", choices=["Backlog", "Ready"], help="初期ステータス")
    p_create.add_argument("--labels", default=None, help="ラベル (カンマ区切り)")
    p_create.add_argument("--assignee", default=None, help="担当者")
    p_create.add_argument("--parent", type=int, default=None, help="親Issue番号")
    p_create.add_argument("--depends-on", default=None, help="依存Issue番号 (カンマ区切り)")

    # --- status ---
    p_status = subparsers.add_parser("status", help="ステータス変更")
    p_status.add_argument("issue_number", type=int, help="Issue番号")
    p_status.add_argument("new_status", choices=VALID_STATUSES, help="新しいステータス")
    p_status.add_argument("--force", action="store_true", help="ゲートチェックをスキップ")
    p_status.add_argument("--with-pr", action="store_true", help="In progress移行時にDraft PRを自動作成")

    # --- list ---
    p_list = subparsers.add_parser("list", help="Issue一覧")
    p_list.add_argument("--status", default=None, help="ステータスでフィルタ")
    p_list.add_argument("--blocked", action="store_true", help="ブロック中のみ表示")
    p_list.add_argument("--assignee", default=None, help="担当者でフィルタ")
    p_list.add_argument("--parent", type=int, default=None, help="親Issue番号で子課題をフィルタ")
    p_list.add_argument("--all", dest="show_all", action="store_true", help="CLOSEDも含めて表示")

    # --- block ---
    p_block = subparsers.add_parser("block", help="ブロック設定")
    p_block.add_argument("issue_number", type=int, help="Issue番号")
    p_block.add_argument("--type", required=True, choices=VALID_BLOCKER_TYPES,
                         dest="blocker_type", help="ブロック種別")
    p_block.add_argument("--reason", default=None, help="理由（Issueコメントに追加）")

    # --- unblock ---
    p_unblock = subparsers.add_parser("unblock", help="ブロック解除")
    p_unblock.add_argument("issue_number", type=int, help="Issue番号")
    p_unblock.add_argument("--comment", default=None, help="解除理由（Issueコメントに追加）")

    # --- sync-parents ---
    p_sync = subparsers.add_parser("sync-parents", help="親子課題同期")
    p_sync.add_argument("--dry-run", action="store_true", help="変更を反映せず差分のみ確認")

    # --- weekly-check ---
    subparsers.add_parser("weekly-check", help="週次メンテナンスチェック")

    args = parser.parse_args()
    client = ProjectClient(args.org, args.project, args.repo)

    handlers = {
        "create": cmd_create,
        "status": cmd_status,
        "list": cmd_list,
        "block": cmd_block,
        "unblock": cmd_unblock,
        "sync-parents": cmd_sync_parents,
        "weekly-check": cmd_weekly_check,
    }

    sys.exit(handlers[args.command](args, client))


if __name__ == "__main__":
    main()
