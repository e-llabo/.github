#!/usr/bin/env bash
set -euo pipefail

PROJECT_OWNER="${PROJECT_OWNER:?PROJECT_OWNER is required}"
PROJECT_NUMBER="${PROJECT_NUMBER:?PROJECT_NUMBER is required}"
REPOSITORY="${REPOSITORY:?REPOSITORY is required}"
EVENT_NAME="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME is required}"
EVENT_PATH="${GITHUB_EVENT_PATH:?GITHUB_EVENT_PATH is required}"
DRY_RUN="${DRY_RUN:-false}"

REPO_OWNER="${REPOSITORY%/*}"
REPO_NAME="${REPOSITORY#*/}"

log() {
  echo "[project-status-sync] $*"
}

declare -A STATUS_OPTION_ID
declare -A ISSUE_ITEM_ID_BY_NUMBER
declare -A PR_ITEM_ID_BY_NUMBER
declare -A CURRENT_STATUS_BY_ITEM_ID
declare -A ITEM_LABEL_BY_ID

PROJECT_ID=""
STATUS_FIELD_ID=""

load_project_meta() {
  local field_json
  PROJECT_ID=$(gh project view "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json -q .id)
  field_json=$(gh project field-list "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json)
  STATUS_FIELD_ID=$(jq -r '.fields[] | select(.name=="Status") | .id' <<< "$field_json")

  if [[ -z "$PROJECT_ID" || "$PROJECT_ID" == "null" ]]; then
    echo "Project not found: owner=$PROJECT_OWNER number=$PROJECT_NUMBER" >&2
    exit 1
  fi
  if [[ -z "$STATUS_FIELD_ID" || "$STATUS_FIELD_ID" == "null" ]]; then
    echo "Status field not found" >&2
    exit 1
  fi

  local statuses=("Backlog" "Ready" "In progress" "In review" "Approved" "Staging / Merged to Release" "Done")
  local status
  for status in "${statuses[@]}"; do
    local id
    id=$(jq -r --arg name "$status" '.fields[] | select(.name=="Status") | .options[]? | select(.name==$name) | .id' <<< "$field_json")
    if [[ -z "$id" || "$id" == "null" ]]; then
      echo "Status option not found: $status" >&2
      exit 1
    fi
    STATUS_OPTION_ID["$status"]="$id"
  done
}

load_project_items() {
  local after=""
  local has_next="true"

  while [[ "$has_next" == "true" ]]; do
    local page
    if [[ -n "$after" ]]; then
      page=$(gh api graphql -f query='query($projectId:ID!, $after:String) { node(id:$projectId) { ... on ProjectV2 { items(first:100, after:$after) { pageInfo { hasNextPage endCursor } nodes { id content { __typename ... on Issue { number repository { name owner { login } } } ... on PullRequest { number repository { name owner { login } } } } status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }' -F projectId="$PROJECT_ID" -F after="$after")
    else
      page=$(gh api graphql -f query='query($projectId:ID!) { node(id:$projectId) { ... on ProjectV2 { items(first:100) { pageInfo { hasNextPage endCursor } nodes { id content { __typename ... on Issue { number repository { name owner { login } } } ... on PullRequest { number repository { name owner { login } } } } status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }' -F projectId="$PROJECT_ID")
    fi

    if jq -e '.errors' >/dev/null 2>&1 <<< "$page"; then
      echo "GraphQL error while loading project items: $(jq -c '.errors' <<< "$page")" >&2
      exit 1
    fi

    while IFS=$'\t' read -r item_id type repo_owner repo_name number status; do
      if [[ "$repo_owner" != "$REPO_OWNER" || "$repo_name" != "$REPO_NAME" ]]; then
        continue
      fi

      CURRENT_STATUS_BY_ITEM_ID["$item_id"]="$status"
      if [[ "$type" == "Issue" ]]; then
        ISSUE_ITEM_ID_BY_NUMBER["$number"]="$item_id"
        ITEM_LABEL_BY_ID["$item_id"]="Issue #$number"
      elif [[ "$type" == "PullRequest" ]]; then
        PR_ITEM_ID_BY_NUMBER["$number"]="$item_id"
        ITEM_LABEL_BY_ID["$item_id"]="PR #$number"
      fi
    done < <(jq -r '.data.node.items.nodes // [] | .[] | [ .id, .content.__typename, (.content.repository.owner.login // ""), (.content.repository.name // ""), (.content.number|tostring), (.status.name // "<null>") ] | @tsv' <<< "$page")

    has_next=$(jq -r '.data.node.items.pageInfo.hasNextPage' <<< "$page")
    after=$(jq -r '.data.node.items.pageInfo.endCursor // ""' <<< "$page")
  done
}

set_item_status() {
  local item_id="$1"
  local target_status="$2"
  local current_status="${CURRENT_STATUS_BY_ITEM_ID[$item_id]:-<null>}"

  if [[ "$current_status" == "$target_status" ]]; then
    log "skip ${ITEM_LABEL_BY_ID[$item_id]} status=$current_status"
    return 0
  fi

  local option_id="${STATUS_OPTION_ID[$target_status]}"
  if [[ -z "$option_id" ]]; then
    echo "Unknown target status: $target_status" >&2
    exit 1
  fi

  if [[ "$DRY_RUN" == "true" ]]; then
    log "DRY-RUN ${ITEM_LABEL_BY_ID[$item_id]}: $current_status -> $target_status"
    return 0
  fi

  gh api graphql \
    -f query='mutation($project:ID!, $item:ID!, $field:ID!, $option:String!) { updateProjectV2ItemFieldValue(input:{ projectId:$project, itemId:$item, fieldId:$field, value:{ singleSelectOptionId:$option } }) { projectV2Item { id } } }' \
    -F project="$PROJECT_ID" \
    -F item="$item_id" \
    -F field="$STATUS_FIELD_ID" \
    -F option="$option_id" >/dev/null

  log "updated ${ITEM_LABEL_BY_ID[$item_id]}: $current_status -> $target_status"
}

apply_to_pr_and_linked_issues() {
  local pr_number="$1"
  local target_status="$2"

  local pr_item_id="${PR_ITEM_ID_BY_NUMBER[$pr_number]:-}"
  if [[ -n "$pr_item_id" ]]; then
    set_item_status "$pr_item_id" "$target_status"
  else
    log "skip PR #$pr_number (project item not found)"
  fi

  local issue_numbers
  issue_numbers=$(gh pr view "$pr_number" --repo "$REPOSITORY" --json closingIssuesReferences --jq '.closingIssuesReferences[]?.number')
  if [[ -z "$issue_numbers" ]]; then
    return 0
  fi

  local issue_no
  while IFS= read -r issue_no; do
    [[ -z "$issue_no" ]] && continue
    local issue_item_id="${ISSUE_ITEM_ID_BY_NUMBER[$issue_no]:-}"
    if [[ -n "$issue_item_id" ]]; then
      set_item_status "$issue_item_id" "$target_status"
    else
      log "skip Issue #$issue_no (project item not found)"
    fi
  done <<< "$issue_numbers"
}

handle_pull_request_review() {
  local review_state
  review_state=$(jq -r '.review.state // ""' "$EVENT_PATH")
  local pr_number
  pr_number=$(jq -r '.pull_request.number // empty' "$EVENT_PATH")

  [[ -z "$pr_number" ]] && return 0

  case "$review_state" in
    approved)
      apply_to_pr_and_linked_issues "$pr_number" "Approved"
      ;;
    changes_requested)
      apply_to_pr_and_linked_issues "$pr_number" "In progress"
      ;;
    *)
      log "no-op review.state=$review_state"
      ;;
  esac
}

handle_pull_request() {
  local action
  action=$(jq -r '.action // ""' "$EVENT_PATH")
  local pr_number
  pr_number=$(jq -r '.pull_request.number // empty' "$EVENT_PATH")
  local is_draft
  is_draft=$(jq -r '.pull_request.draft // false' "$EVENT_PATH")
  local merged
  merged=$(jq -r '.pull_request.merged // false' "$EVENT_PATH")

  [[ -z "$pr_number" ]] && return 0

  case "$action" in
    opened|synchronize|ready_for_review)
      if [[ "$is_draft" == "true" ]]; then
        apply_to_pr_and_linked_issues "$pr_number" "In progress"
      else
        apply_to_pr_and_linked_issues "$pr_number" "In review"
      fi
      ;;
    reopened|converted_to_draft)
      apply_to_pr_and_linked_issues "$pr_number" "In progress"
      ;;
    closed)
      if [[ "$merged" == "true" ]]; then
        apply_to_pr_and_linked_issues "$pr_number" "Staging / Merged to Release"
      else
        log "no-op PR closed without merge"
      fi
      ;;
    *)
      log "no-op pull_request action=$action"
      ;;
  esac
}

handle_issues() {
  local action
  action=$(jq -r '.action // ""' "$EVENT_PATH")
  local issue_number
  issue_number=$(jq -r '.issue.number // empty' "$EVENT_PATH")

  [[ -z "$issue_number" ]] && return 0

  local item_id="${ISSUE_ITEM_ID_BY_NUMBER[$issue_number]:-}"
  if [[ -z "$item_id" ]]; then
    log "skip Issue #$issue_number (project item not found)"
    return 0
  fi

  case "$action" in
    closed)
      set_item_status "$item_id" "Done"
      ;;
    reopened)
      set_item_status "$item_id" "In progress"
      ;;
    *)
      log "no-op issues action=$action"
      ;;
  esac
}

main() {
  load_project_meta
  load_project_items

  case "$EVENT_NAME" in
    pull_request_review)
      handle_pull_request_review
      ;;
    pull_request)
      handle_pull_request
      ;;
    issues)
      handle_issues
      ;;
    *)
      log "unsupported event: $EVENT_NAME"
      ;;
  esac
}

main "$@"
