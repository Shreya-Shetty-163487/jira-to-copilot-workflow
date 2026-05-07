import json
import os
import sys
import time

import requests
from dotenv import load_dotenv
from github import Github, GithubException


COPILOT_ASSIGNEE = "copilot-swe-agent[bot]"
JIRA_LINK_MARKER = "<!-- jira-key:"
COPILOT_POLL_INTERVAL = 30  # seconds
COPILOT_POLL_TIMEOUT = 300  # 5 minutes
QUALITY_PASS_SCORE = 6
GITHUB_MODELS_ENDPOINT = "https://models.inference.ai.azure.com/chat/completions"
GITHUB_MODELS_MODEL = "gpt-4o"


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _get_github_token() -> str:
    """Get GitHub token — checks GH_PAT_TOKEN (Actions) and GITHUB_TOKEN (local)."""
    token = os.getenv("GH_PAT_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("Missing required environment variable: GITHUB_TOKEN or GH_PAT_TOKEN")
    return token


def validate_ticket_quality(ticket: dict) -> dict:
    """Score the Jira ticket for clarity and completeness using GitHub Models (GPT-4o).
    Returns {score, dimension_scores, missing, improvements, rewritten_title,
             rewritten_description, example_acceptance_criteria, example_edge_cases}.
    """
    load_dotenv()
    token = _get_github_token()

    prompt = f"""You are a senior engineering manager reviewing a Jira ticket before it gets assigned to an AI coding agent.
Score the ticket strictly from 0 to 10 based on how actionable and unambiguous it is for an AI to implement without any human follow-up.

Scoring criteria:
- Title clarity (is it specific, not vague like 'fix bug') [0-2]
- Description completeness (what to build, not just what's wrong) [0-2]
- Acceptance criteria present and testable [0-2]
- Edge cases or error scenarios mentioned [0-2]
- No ambiguous words like 'improve', 'enhance', 'maybe', 'somehow' [0-2]

JIRA TICKET:
Title: {ticket.get('title', '')}
Type: {ticket.get('issue_type', '')}
Description: {ticket.get('description', '') or '(empty)'}

Respond with ONLY valid JSON — no explanation, no markdown fences.
{{
  "score": <integer 0-10>,
  "dimension_scores": {{
    "title_clarity": <0-2>,
    "description_completeness": <0-2>,
    "acceptance_criteria": <0-2>,
    "edge_cases": <0-2>,
    "no_ambiguity": <0-2>
  }},
  "missing": ["<section completely absent>"],
  "improvements": ["<specific problem with current content>"],
  "rewritten_title": "<actionable rewrite of the title>",
  "rewritten_description": "<2-3 sentence rewrite stating exactly what to build>",
  "example_acceptance_criteria": [
    "<testable criterion 1>",
    "<testable criterion 2>",
    "<testable criterion 3>"
  ],
  "example_edge_cases": [
    "<edge case 1>",
    "<edge case 2>"
  ]
}}"""

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GITHUB_MODELS_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(GITHUB_MODELS_ENDPOINT, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"GitHub Models API call failed: {exc}") from exc

    raw_content = resp.json()["choices"][0]["message"]["content"] or ""

    # Strip markdown fences if the model wraps the JSON anyway
    stripped = raw_content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0].strip()

    try:
        result = json.loads(stripped)
    except json.JSONDecodeError:
        raise RuntimeError(f"Ticket quality check returned invalid JSON: {raw_content[:300]}")

    return {
        "score": int(result.get("score", 0)),
        "dimension_scores": result.get("dimension_scores", {}),
        "missing": result.get("missing", []),
        "improvements": result.get("improvements", []),
        "rewritten_title": result.get("rewritten_title", ""),
        "rewritten_description": result.get("rewritten_description", ""),
        "example_acceptance_criteria": result.get("example_acceptance_criteria", []),
        "example_edge_cases": result.get("example_edge_cases", []),
    }


def format_ticket_as_issue(ticket: dict) -> str:
    """Format a Jira ticket directly into a structured GitHub Issue body without LLM."""
    title = ticket.get("title", "")
    description = ticket.get("description", "") or "(No description provided)"
    issue_type = ticket.get("issue_type", "Task")
    labels = ticket.get("labels", [])

    sections = []

    sections.append(f"## {title}")
    sections.append("")
    sections.append(f"**Type:** {issue_type}")
    if labels:
        sections.append(f"**Labels:** {', '.join(labels)}")
    sections.append("")

    sections.append("## Description")
    sections.append("")
    sections.append(description)
    sections.append("")

    sections.append("## Definition of done")
    sections.append("")
    sections.append("- [ ] Code implemented")
    sections.append("- [ ] Tests written and passing")
    sections.append("- [ ] No breaking changes to existing endpoints/pages")

    return "\n".join(sections)


def _get_existing_copilot_label(repo) -> list[str]:
    try:
        repo.get_label("copilot")
        return ["copilot"]
    except GithubException as exc:
        if exc.status == 404:
            return []
        raise


def _get_repo():
    load_dotenv()

    token = _get_github_token()
    owner = _required_env("GITHUB_OWNER")
    repo_name = _required_env("GITHUB_REPO")

    github = Github(token)
    return github.get_repo(f"{owner}/{repo_name}")


def find_existing_issue(jira_key: str):
    """Check if a GitHub issue already exists for this Jira key. Returns the issue or None."""
    load_dotenv()
    token = _get_github_token()
    owner = _required_env("GITHUB_OWNER")
    repo_name = _required_env("GITHUB_REPO")

    g = Github(token)
    query = f"repo:{owner}/{repo_name} is:issue is:open \"{JIRA_LINK_MARKER} {jira_key}\""
    issues = g.search_issues(query)
    for issue in issues:
        if f"{JIRA_LINK_MARKER} {jira_key}" in (issue.body or ""):
            return issue
    return None


def append_jira_link(body: str, jira_key: str, jira_base_url: str | None = None) -> str:
    """Append a Jira ticket link and hidden marker to the issue body for traceability."""
    base_url = (jira_base_url or os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    jira_url = f"{base_url}/browse/{jira_key}" if base_url else jira_key

    footer = (
        f"\n\n---\n"
        f"**Jira ticket:** [{jira_key}]({jira_url})\n"
        f"{JIRA_LINK_MARKER} {jira_key} -->"
    )
    return body + footer


def wait_for_copilot_activity(issue, timeout: int = COPILOT_POLL_TIMEOUT) -> bool:
    """Poll the issue for Copilot activity (comment or linked PR). Returns True if activity detected."""
    elapsed = 0
    while elapsed < timeout:
        time.sleep(COPILOT_POLL_INTERVAL)
        elapsed += COPILOT_POLL_INTERVAL

        # Check for comments from copilot bot
        comments = issue.get_comments()
        for comment in comments:
            if comment.user and "copilot" in (comment.user.login or "").lower():
                return True

        # Check for linked pull requests via timeline events
        try:
            events = issue.get_timeline()
            for event in events:
                if event.event == "cross-referenced":
                    source = getattr(event, "source", None)
                    if source and hasattr(source, "issue"):
                        pr = source.issue
                        if hasattr(pr, "pull_request") and pr.pull_request:
                            return True
        except Exception:
            pass  # Timeline API may not be available, continue polling

    return False


def _ensure_label_exists(repo, label_name: str) -> None:
    """Create a label if it doesn't already exist."""
    try:
        repo.get_label(label_name)
    except GithubException as exc:
        if exc.status == 404:
            repo.create_label(name=label_name, color="1d76db")
        else:
            raise


def create_issue(title: str, body: str, jira_key: str | None = None):
    """Create a GitHub issue with the optional copilot label and a jira:<key> label."""
    repo = _get_repo()
    labels = _get_existing_copilot_label(repo)

    if jira_key:
        jira_label = f"jira:{jira_key}"
        _ensure_label_exists(repo, jira_label)
        labels.append(jira_label)

    try:
        return repo.create_issue(
            title=title,
            body=body,
            labels=labels,
        )
    except GithubException as exc:
        message = str(exc.data) if getattr(exc, "data", None) else str(exc)
        raise RuntimeError(f"GitHub issue creation failed: {message}") from exc


def assign_issue_to_copilot(issue) -> None:
    """Assign an existing GitHub issue to the Copilot coding agent.
    Falls back to posting a @copilot comment if the assignee API fails."""
    try:
        issue.add_to_assignees(COPILOT_ASSIGNEE)
        return
    except GithubException as exc:
        print(f"[WARN] Assignee API failed (status={getattr(exc, 'status', '?')}), falling back to @copilot comment.")

    # Fallback: trigger Copilot via @copilot comment
    try:
        issue.create_comment("@copilot implement this issue.")
    except GithubException as exc:
        message = str(exc.data) if getattr(exc, "data", None) else str(exc)
        raise RuntimeError(f"Failed to trigger Copilot via comment: {message}") from exc


def create_and_assign_issue(title: str, body: str, jira_key: str | None = None) -> str:
    """Create a GitHub issue and assign it to the Copilot coding agent."""
    issue = create_issue(title, body, jira_key=jira_key)
    assign_issue_to_copilot(issue)
    return issue.html_url


def process_ticket(issue_key: str) -> str:
    """Fetch Jira ticket details, format them, and create a Copilot-assigned GitHub issue."""
    from fetch_ticket import fetch_ticket

    ticket = fetch_ticket(issue_key)
    body = format_ticket_as_issue(ticket)
    body = append_jira_link(body, issue_key)
    return create_and_assign_issue(ticket["title"], body, jira_key=issue_key)
