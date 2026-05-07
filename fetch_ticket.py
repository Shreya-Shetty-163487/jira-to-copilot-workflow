import json
import os
import re
import sys
from typing import Any

import requests
from dotenv import load_dotenv


def extract_text_from_adf(doc: Any) -> str:
    """Extract plain text from Atlassian Document Format, preserving paragraph breaks."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        if node is None:
            return

        if isinstance(node, str):
            parts.append(node)
            return

        if isinstance(node, list):
            for child in node:
                walk(child)
            return

        if not isinstance(node, dict):
            return

        node_type = node.get("type")

        if node_type == "text":
            parts.append(node.get("text", ""))
            return

        if node_type == "hardBreak":
            parts.append("\n")
            return

        if node_type == "mention":
            attrs = node.get("attrs", {})
            parts.append(attrs.get("text") or attrs.get("displayName") or "")
            return

        for child in node.get("content", []):
            walk(child)

        if node_type in {"paragraph", "heading", "blockquote", "listItem"}:
            parts.append("\n\n")
        elif node_type in {"bulletList", "orderedList"}:
            parts.append("\n")

    walk(doc)

    text = "".join(parts)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def fetch_ticket(issue_key: str) -> dict:
    """Fetch a Jira issue by key and return normalized ticket details."""
    load_dotenv()

    base_url = _required_env("JIRA_BASE_URL").rstrip("/")
    email = _required_env("JIRA_EMAIL")
    api_token = _required_env("JIRA_API_TOKEN")

    response = requests.get(
        f"{base_url}/rest/api/3/issue/{issue_key}",
        params={"fields": "summary,description,issuetype,labels,status,reporter"},
        auth=(email, api_token),
        headers={"Accept": "application/json"},
        timeout=30,
    )

    if response.status_code == 401:
        raise RuntimeError("Jira authentication failed: check JIRA_EMAIL and JIRA_API_TOKEN.")

    if response.status_code == 404:
        raise RuntimeError(f"Jira ticket not found: {issue_key}")

    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Jira API request failed with status {response.status_code}: {response.text}") from exc

    issue = response.json()
    fields = issue.get("fields", {})
    reporter = fields.get("reporter") or {}

    return {
        "key": issue.get("key"),
        "title": fields.get("summary"),
        "description": extract_text_from_adf(fields.get("description")),
        "issue_type": (fields.get("issuetype") or {}).get("name"),
        "labels": fields.get("labels") or [],
        "status": (fields.get("status") or {}).get("name"),
        "reporter": reporter.get("displayName") or reporter.get("emailAddress") or reporter.get("accountId"),
    }


def transition_ticket(issue_key: str, target_status: str) -> bool:
    """Transition a Jira ticket to the target status. Returns True if successful."""
    load_dotenv()

    base_url = _required_env("JIRA_BASE_URL").rstrip("/")
    email = _required_env("JIRA_EMAIL")
    api_token = _required_env("JIRA_API_TOKEN")

    # Get available transitions
    response = requests.get(
        f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
        auth=(email, api_token),
        headers={"Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()

    transitions = response.json().get("transitions", [])
    target_transition = None
    for t in transitions:
        if t["name"].lower() == target_status.lower():
            target_transition = t
            break

    if not target_transition:
        available = [t["name"] for t in transitions]
        raise RuntimeError(
            f"Cannot transition {issue_key} to '{target_status}'. "
            f"Available transitions: {available}"
        )

    # Execute the transition
    response = requests.post(
        f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
        auth=(email, api_token),
        headers={"Content-Type": "application/json"},
        json={"transition": {"id": target_transition["id"]}},
        timeout=30,
    )
    response.raise_for_status()
    return True


def post_comment(issue_key: str, text: str) -> None:
    """Post a plain-text comment on a Jira issue using Atlassian Document Format."""
    load_dotenv()

    base_url = _required_env("JIRA_BASE_URL").rstrip("/")
    email = _required_env("JIRA_EMAIL")
    api_token = _required_env("JIRA_API_TOKEN")

    # Convert plain text paragraphs to ADF
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    adf_content = [
        {"type": "paragraph", "content": [{"type": "text", "text": p}]}
        for p in paragraphs
    ]

    response = requests.post(
        f"{base_url}/rest/api/3/issue/{issue_key}/comment",
        auth=(email, api_token),
        headers={"Content-Type": "application/json"},
        json={"body": {"type": "doc", "version": 1, "content": adf_content}},
        timeout=30,
    )
    response.raise_for_status()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fetch_ticket.py <ISSUE_KEY>", file=sys.stderr)
        sys.exit(1)

    try:
        ticket = fetch_ticket(sys.argv[1])
        print(json.dumps(ticket, indent=2))
    except RuntimeError as err:
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
