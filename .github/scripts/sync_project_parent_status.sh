#!/usr/bin/env bash
set -euo pipefail

PROJECT_OWNER="${PROJECT_OWNER:?PROJECT_OWNER is required}"
PROJECT_NUMBER="${PROJECT_NUMBER:?PROJECT_NUMBER is required}"
REPOSITORY="${REPOSITORY:?REPOSITORY is required}"
DRY_RUN="${DRY_RUN:-false}"

log() {
  echo "[project-parent-sync] $*"
}

require_option_id() {
  local name="$1"
  local field_json="$2"
  local id
  id=$(jq -r --arg n "$name" '.fields[] | select(.name=="Status") | .options[] | select(.name==$n) | .id' <<< "$field_json")
  if [[ -z "$id" || "$id" == "null" ]]; then
    echo "Status option '$name' が見つかりません" >&2
    exit 1
  fi
  echo "$id"
}

compute_parent_target() {
  local statuses_str="$1"
  IFS='|' read -r -a arr <<< "$statuses_str"

  local any_in_progress=0
  local any_in_review=0
  local any_approved_or_staging=0
  local all_done=1
  local all_ready_or_backlog=1

  for raw in "${arr[@]}"; do
    local s="$raw"
    if [[ "$s" == "<null>" ]]; then
      s="Backlog"
    fi

    [[ "$s" == "In progress" ]] && any_in_progress=1
    [[ "$s" == "In review" ]] && any_in_review=1
    [[ "$s" == "Approved" || "$s" == "Staging / Merged to Release" ]] && any_approved_or_staging=1

    if [[ "$s" != "Done" ]]; then
      all_done=0
    fi
    if [[ "$s" != "Ready" && "$s" != "Backlog" ]]; then
      all_ready_or_backlog=0
    fi
  done

  if (( any_in_progress == 1 )); then
    echo "In progress"
    return
  fi
  if (( all_done == 1 )); then
    echo "Done"
    return
  fi
  if (( any_in_review == 1 )); then
    echo "In review"
    return
  fi
  if (( any_approved_or_staging == 1 )); then
    echo "Approved"
    return
  fi
  if (( all_ready_or_backlog == 1 )); then
    echo "Ready"
    return
  fi
  echo "Ready"
}

set_project_status() {
  local project_id="$1"
  local item_id="$2"
  local status_field_id="$3"
  local option_id="$4"
  gh api graphql -f query="mutation { updateProjectV2ItemFieldValue(input:{ projectId:\"$project_id\", itemId:\"$item_id\", fieldId:\"$status_field_id\", value:{ singleSelectOptionId:\"$option_id\" } }) { projectV2Item { id } } }" >/dev/null
}

project_id=$(gh project view "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json -q .id)
field_json=$(gh project field-list "$PROJECT_NUMBER" --owner "$PROJECT_OWNER" --format json)
status_field_id=$(jq -r '.fields[] | select(.name=="Status") | .id' <<< "$field_json")

if [[ -z "$status_field_id" || "$status_field_id" == "null" ]]; then
  echo "Status field が見つかりません" >&2
  exit 1
fi

declare -A STATUS_OPTION_ID
STATUS_OPTION_ID["Ready"]=$(require_option_id "Ready" "$field_json")
STATUS_OPTION_ID["In progress"]=$(require_option_id "In progress" "$field_json")
STATUS_OPTION_ID["In review"]=$(require_option_id "In review" "$field_json")
STATUS_OPTION_ID["Approved"]=$(require_option_id "Approved" "$field_json")
STATUS_OPTION_ID["Done"]=$(require_option_id "Done" "$field_json")

log "project_id=$project_id dry_run=$DRY_RUN"

all_nodes='[]'
after=""
while : ; do
  if [[ -n "$after" ]]; then
    page=$(gh api graphql -f query='query($projectId:ID!, $after:String) { node(id:$projectId) { ... on ProjectV2 { items(first:100, after:$after) { pageInfo { hasNextPage endCursor } nodes { id content { __typename ... on Issue { number state parent { number } } } status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }' -F projectId="$project_id" -F after="$after")
  else
    page=$(gh api graphql -f query='query($projectId:ID!) { node(id:$projectId) { ... on ProjectV2 { items(first:100) { pageInfo { hasNextPage endCursor } nodes { id content { __typename ... on Issue { number state parent { number } } } status: fieldValueByName(name:"Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } } } } }' -F projectId="$project_id")
  fi

  nodes=$(jq '.data.node.items.nodes' <<< "$page")
  all_nodes=$(jq -nc --argjson a "$all_nodes" --argjson b "$nodes" '$a + $b')

  has_next=$(jq -r '.data.node.items.pageInfo.hasNextPage' <<< "$page")
  end_cursor=$(jq -r '.data.node.items.pageInfo.endCursor // ""' <<< "$page")

  if [[ "$has_next" != "true" ]]; then
    break
  fi
  after="$end_cursor"
done

tmp_json=$(mktemp)
printf '%s' "$all_nodes" > "$tmp_json"

mapfile -t issue_rows < <(jq -r '.[] | select(.content.__typename=="Issue") | [(.content.number|tostring), .id, (.content.state // ""), (.status.name // "<null>"), ((.content.parent.number // "")|tostring)] | @tsv' "$tmp_json")

declare -A ITEM_ID_BY_ISSUE
declare -A ISSUE_STATE_BY_ISSUE
declare -A STATUS_BY_ISSUE
declare -A CHILD_STATUS_BY_PARENT
declare -A CHILD_STATE_BY_PARENT
declare -A CHILD_COUNT_BY_PARENT

for row in "${issue_rows[@]}"; do
  IFS=$'\t' read -r issue_no item_id issue_state status parent_no <<< "$row"
  ITEM_ID_BY_ISSUE["$issue_no"]="$item_id"
  ISSUE_STATE_BY_ISSUE["$issue_no"]="$issue_state"
  STATUS_BY_ISSUE["$issue_no"]="$status"

  if [[ -n "$parent_no" ]]; then
    if [[ -z "${CHILD_STATUS_BY_PARENT[$parent_no]:-}" ]]; then
      CHILD_STATUS_BY_PARENT["$parent_no"]="$status"
      CHILD_STATE_BY_PARENT["$parent_no"]="$issue_state"
      CHILD_COUNT_BY_PARENT["$parent_no"]=1
    else
      CHILD_STATUS_BY_PARENT["$parent_no"]+="|$status"
      CHILD_STATE_BY_PARENT["$parent_no"]+="|$issue_state"
      CHILD_COUNT_BY_PARENT["$parent_no"]=$((CHILD_COUNT_BY_PARENT[$parent_no] + 1))
    fi
  fi
done

status_updates=0
close_updates=0
checked_parents=0

for parent_no in "${!CHILD_COUNT_BY_PARENT[@]}"; do
  if [[ -z "${ITEM_ID_BY_ISSUE[$parent_no]:-}" ]]; then
    continue
  fi

  checked_parents=$((checked_parents + 1))
  target_status=$(compute_parent_target "${CHILD_STATUS_BY_PARENT[$parent_no]}")
  current_status="${STATUS_BY_ISSUE[$parent_no]}"

  if [[ "$current_status" != "$target_status" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
      log "DRY-RUN status parent #$parent_no: $current_status -> $target_status"
    else
      set_project_status "$project_id" "${ITEM_ID_BY_ISSUE[$parent_no]}" "$status_field_id" "${STATUS_OPTION_ID[$target_status]}"
      log "status parent #$parent_no: $current_status -> $target_status"
    fi
    status_updates=$((status_updates + 1))
  fi

  parent_issue_state="${ISSUE_STATE_BY_ISSUE[$parent_no]}"
  all_children_closed=1
  IFS='|' read -r -a child_states <<< "${CHILD_STATE_BY_PARENT[$parent_no]}"
  for st in "${child_states[@]}"; do
    if [[ "$st" != "CLOSED" ]]; then
      all_children_closed=0
      break
    fi
  done

  if [[ "$parent_issue_state" == "OPEN" && "$all_children_closed" == "1" ]]; then
    if [[ "$DRY_RUN" == "true" ]]; then
      log "DRY-RUN close parent issue #$parent_no"
    else
      gh issue close "$parent_no" --repo "$REPOSITORY" >/dev/null
      log "close parent issue #$parent_no"
    fi
    close_updates=$((close_updates + 1))
  fi
done

log "checked_parents=$checked_parents status_updates=$status_updates close_updates=$close_updates"
