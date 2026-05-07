import argparse
import os
import sys

from dotenv import load_dotenv

from create_github_issue import (
    append_jira_link,
    assign_issue_to_copilot,
    create_issue,
    find_existing_issue,
    format_ticket_as_issue,
    validate_ticket_quality,
    wait_for_copilot_activity,
    QUALITY_PASS_SCORE,
)
from fetch_ticket import fetch_ticket, post_comment, transition_ticket


def _jira_url(issue_key: str) -> str:
    base_url = (os.getenv("JIRA_BASE_URL") or "").rstrip("/")
    if not base_url:
        return issue_key
    return f"{base_url}/browse/{issue_key}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a Copilot-ready GitHub issue from a Jira ticket.")
    parser.add_argument("issue_key", help="Jira issue key, such as PROJ-123")
    parser.add_argument("--dry-run", action="store_true", help="Format the ticket without creating an issue")
    return parser.parse_args()


def process_ticket(issue_key: str, dry_run: bool = False) -> str | None:
    # Step 1: Check for duplicate — avoid creating a second issue for the same Jira key
    print(f"Checking for existing GitHub issue for {issue_key}...")
    existing = find_existing_issue(issue_key)
    if existing:
        print(f"Issue already exists: {existing.html_url}")
        print("Skipping creation to avoid duplicates.")
        return existing.html_url

    # Step 2: Fetch Jira ticket details
    print(f"Fetching Jira ticket {issue_key}...")
    ticket = fetch_ticket(issue_key)
    print(f"Title: {ticket.get('title')}")
    print(f"Type: {ticket.get('issue_type')}")

    # Step 3: Validate ticket quality before touching GitHub
    print("Validating ticket quality with GPT-4o (GitHub Models)...")
    quality = validate_ticket_quality(ticket)
    score = quality["score"]
    dim = quality.get("dimension_scores", {})
    print(f"Quality score: {score}/10")
    if dim:
        print(f"  Title clarity:        {dim.get('title_clarity', '?')}/2")
        print(f"  Description:          {dim.get('description_completeness', '?')}/2")
        print(f"  Acceptance criteria:  {dim.get('acceptance_criteria', '?')}/2")
        print(f"  Edge cases:           {dim.get('edge_cases', '?')}/2")
        print(f"  No ambiguity:         {dim.get('no_ambiguity', '?')}/2")

    if score < QUALITY_PASS_SCORE:
        print(f"\nTicket quality too low ({score}/10, minimum {QUALITY_PASS_SCORE}/10). Blocking pipeline.")

        missing = quality.get("missing", [])
        improvements = quality.get("improvements", [])
        rewritten_title = quality.get("rewritten_title", "")
        rewritten_description = quality.get("rewritten_description", "")
        examples = quality.get("example_acceptance_criteria", [])
        edge_cases = quality.get("example_edge_cases", [])

        comment_lines = [
            "Automated pipeline blocked - ticket quality too low.",
            "",
            f"Quality score: {score}/10 (minimum required: {QUALITY_PASS_SCORE}/10)",
            "",
            "Score breakdown:",
            f"  Title clarity:        {dim.get('title_clarity', '?')}/2",
            f"  Description:          {dim.get('description_completeness', '?')}/2",
            f"  Acceptance criteria:  {dim.get('acceptance_criteria', '?')}/2",
            f"  Edge cases:           {dim.get('edge_cases', '?')}/2",
            f"  No ambiguity:         {dim.get('no_ambiguity', '?')}/2",
        ]
        if missing:
            comment_lines += ["", "What is completely MISSING (must be added):"]
            comment_lines += [f"  - {m}" for m in missing]
        if improvements:
            comment_lines += ["", "What needs to be IMPROVED:"]
            comment_lines += [f"  - {imp}" for imp in improvements]
        if rewritten_title:
            comment_lines += ["", "Suggested title rewrite:", f"  {rewritten_title}"]
        if rewritten_description:
            comment_lines += ["", "Suggested description rewrite:", f"  {rewritten_description}"]
        if examples:
            comment_lines += ["", "Example acceptance criteria to add:"]
            comment_lines += [f"  - {ex}" for ex in examples]
        if edge_cases:
            comment_lines += ["", "Edge cases to cover:"]
            comment_lines += [f"  - {ec}" for ec in edge_cases]
        comment_lines += [
            "",
            "-" * 45,
            f"Fix the above and re-run: python main.py {issue_key}",
        ]

        try:
            post_comment(issue_key, "\n".join(comment_lines))
            print(f"Feedback posted to Jira ticket {issue_key}.")
        except Exception as exc:
            print(f"Warning: Could not post feedback to Jira: {exc}")

        return None

    print(f"Ticket quality OK ({score}/10). Proceeding...")

    # Step 4: Format issue body directly from ticket fields
    print("Formatting issue body...")
    body = format_ticket_as_issue(ticket)

    # Step 5: Append Jira link and traceability marker
    body = append_jira_link(body, issue_key)

    if dry_run:
        print("\n--- GitHub Issue Body ---\n")
        print(body)
        return None

    # Step 6: Create GitHub issue
    print("Creating GitHub issue...")
    issue = create_issue(ticket["title"], body)
    issue_url = issue.html_url
    print(f"Issue URL: {issue_url}")

    # Step 7: Assign to Copilot coding agent — triggers automatic implementation
    print("Assigning to GitHub Copilot agent...")
    assign_issue_to_copilot(issue)

    # Step 8: Transition Jira ticket to "In Progress"
    print(f"Transitioning Jira ticket {issue_key} to 'In Progress'...")
    try:
        transition_ticket(issue_key, "In Progress")
        print("Jira ticket status updated.")
    except RuntimeError as exc:
        print(f"Warning: Could not transition Jira ticket: {exc}")

    # Step 9: Wait for Copilot to pick up the issue and start working
    print("Waiting for Copilot to start working (up to 5 minutes)...")
    picked_up = wait_for_copilot_activity(issue)
    if picked_up:
        print("Copilot has picked up the issue and started working!")
    else:
        print("Warning: No Copilot activity detected within timeout. Check the issue manually.")

    jira_url = _jira_url(issue_key)
    print(f"\nDone! Copilot is working on: {issue_url}")
    print("You'll get a PR notification when it's ready for review.")
    print(f"Jira ticket: {jira_url}")
    return issue_url


def main() -> int:
    load_dotenv()
    args = parse_args()

    try:
        process_ticket(
            issue_key=args.issue_key,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
