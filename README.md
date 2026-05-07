# jira-to-copilot-workflow

A reusable GitHub Actions workflow that bridges Jira and GitHub Copilot. Point it at a Jira ticket key and it will:

1. **Fetch** the Jira ticket details (title, description, type, labels)
2. **Score** the ticket quality using GPT-4o (via GitHub Models) — blocks low-quality tickets before they waste Copilot's time
3. **Post feedback** back to Jira if the ticket is too vague (score < 6/10), with specific suggestions and rewrites
4. **Create a GitHub issue** formatted for Copilot with a definition of done and a traceability link back to Jira
5. **Assign the issue to Copilot** (`copilot-swe-agent[bot]`) to trigger automatic implementation
6. **Transition the Jira ticket** to "In Progress"
7. **Wait and confirm** Copilot has picked up the issue (polls for up to 5 minutes)

---

## Prerequisites

Before using this workflow you need:

- A **Jira Cloud** account with API access
- A **GitHub repo** where issues will be created
- **GitHub Copilot coding agent** enabled on that repo (requires Copilot Enterprise or a plan that includes the Copilot agent)
- A GitHub **Personal Access Token (PAT)** with `repo` and `issues` scopes

---

## Quick Start

### Step 1 — Add secrets to your repo

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add all four secrets:

| Secret name | Description | Where to get it |
|---|---|---|
| `JIRA_BASE_URL` | Your Jira instance URL | e.g. `https://yourorg.atlassian.net` |
| `JIRA_EMAIL` | Email you use to log into Jira | Your Jira account email |
| `JIRA_API_TOKEN` | Jira API token | Jira → Profile → **Manage account** → **Security** → **API tokens** → Create token |
| `GH_PAT_TOKEN` | GitHub Personal Access Token | GitHub → **Settings** → **Developer settings** → **Personal access tokens** → Generate new token (classic) with `repo` and `issues` scopes |

---

### Step 2 — Create a caller workflow in your repo

Create the file `.github/workflows/create-issue-from-jira.yml` in **your repo** with this content:

```yaml
name: Create GitHub Issue from Jira

on:
  workflow_dispatch:
    inputs:
      jira_issue_key:
        description: "Jira issue key e.g. PROJ-123"
        required: true

jobs:
  call-automation:
    uses: YOUR_USERNAME/jira-to-copilot-workflow/.github/workflows/jira-to-github-issue.yml@main
    with:
      jira_issue_key: ${{ inputs.jira_issue_key }}
      github_owner: YOUR_GITHUB_USERNAME_OR_ORG
      github_repo: YOUR_REPO_NAME
    secrets:
      JIRA_BASE_URL: ${{ secrets.JIRA_BASE_URL }}
      JIRA_EMAIL: ${{ secrets.JIRA_EMAIL }}
      JIRA_API_TOKEN: ${{ secrets.JIRA_API_TOKEN }}
      GH_PAT_TOKEN: ${{ secrets.GH_PAT_TOKEN }}
```

Replace:
- `YOUR_USERNAME` — the GitHub username/org where this reusable workflow repo lives
- `YOUR_GITHUB_USERNAME_OR_ORG` — the owner of **your** repo (where issues will be created)
- `YOUR_REPO_NAME` — the name of **your** repo (where issues will be created)

---

### Step 3 — Run it

1. Go to your repo on GitHub
2. Click **Actions** → **Create GitHub Issue from Jira**
3. Click **Run workflow**
4. Enter a Jira issue key (e.g. `PROJ-123`)
5. Click **Run workflow**

Watch the logs — the workflow will walk through each step and print results.

---

## Inputs Reference

| Input | Required | Default | Description |
|---|---|---|---|
| `jira_issue_key` | Yes | — | Jira issue key to process e.g. `PROJ-123` |
| `github_owner` | Yes | — | GitHub user or org that owns the target repo |
| `github_repo` | Yes | — | Name of the GitHub repo where the issue will be created |
| `dry_run` | No | `false` | If `true`, fetches and formats the ticket but does **not** create a GitHub issue. Useful for previewing output. |

---

## Secrets Reference

| Secret | Required | Description |
|---|---|---|
| `JIRA_BASE_URL` | Yes | Your Jira Cloud base URL — no trailing slash |
| `JIRA_EMAIL` | Yes | Email address associated with your Jira account |
| `JIRA_API_TOKEN` | Yes | API token generated from your Jira account settings |
| `GH_PAT_TOKEN` | Yes | GitHub PAT with `repo` and `issues` scopes |

---

## How the Quality Gate Works

Before creating any GitHub issue, the workflow scores your Jira ticket out of 10 across five dimensions:

| Dimension | Max score |
|---|---|
| Title clarity (specific, not vague) | 2 |
| Description completeness | 2 |
| Acceptance criteria present and testable | 2 |
| Edge cases or error scenarios mentioned | 2 |
| No ambiguous language ("improve", "somehow") | 2 |

**Minimum passing score: 6/10**

If the ticket scores below 6, the workflow:
- **Does not** create a GitHub issue
- Posts a detailed comment directly on the Jira ticket with:
  - Score breakdown
  - What is missing
  - What needs to be improved
  - A suggested rewrite of the title and description
  - Example acceptance criteria and edge cases to add

Fix the Jira ticket based on the feedback and re-run.

---

## Dry Run Mode

To preview what the GitHub issue body would look like without creating anything:

```yaml
with:
  jira_issue_key: "PROJ-123"
  github_owner: "your-org"
  github_repo: "your-repo"
  dry_run: true
```

Or trigger it from a manual workflow with a `dry_run` input:

```yaml
on:
  workflow_dispatch:
    inputs:
      jira_issue_key:
        required: true
      dry_run:
        description: "Preview only — do not create issue"
        type: boolean
        default: false
```

---

## Triggering Automatically on Jira Status Change

Instead of running manually, you can trigger this workflow automatically when a Jira ticket moves to a certain status by calling the GitHub API from a Jira automation rule.

In **Jira → Project settings → Automation → Create rule**:

- Trigger: **Field value changed** → Status changed to `Ready for Dev`
- Action: **Send web request**
  - URL: `https://api.github.com/repos/YOUR_ORG/YOUR_REPO/actions/workflows/create-issue-from-jira.yml/dispatches`
  - Method: `POST`
  - Headers: `Authorization: Bearer YOUR_GH_PAT`, `Accept: application/vnd.github+json`
  - Body:
    ```json
    {
      "ref": "main",
      "inputs": {
        "jira_issue_key": "{{issue.key}}"
      }
    }
    ```

---

## What Gets Created in GitHub

For each processed ticket, the workflow creates a GitHub issue with:

- The Jira ticket title as the issue title
- A structured body containing:
  - Issue type and labels
  - Full ticket description
  - A definition of done checklist
  - A footer with the Jira ticket link and a hidden traceability marker
- The `copilot` label (if it exists on the repo)
- A `jira:PROJ-123` label for filtering
- Assigned to `copilot-swe-agent[bot]`

---

## Duplicate Prevention

The workflow checks for an existing open GitHub issue with the same Jira key before creating a new one. If a duplicate is found, it prints the existing issue URL and exits without creating anything.

---

## Troubleshooting

**`Jira authentication failed`**
→ Check that `JIRA_EMAIL` and `JIRA_API_TOKEN` are correct. The token must be an API token, not your account password.

**`Jira ticket not found`**
→ Verify the issue key format (e.g. `PROJ-123`) and that the Jira account has permission to view the ticket.

**`GitHub issue creation failed`**
→ Check that `GH_PAT_TOKEN` has the `repo` and `issues` scopes and that the `github_owner`/`github_repo` values are correct.

**`Assignee API failed — falling back to @copilot comment`**
→ This is a soft warning. The workflow still works — it triggers Copilot via a comment instead of the assignee API. This usually means the Copilot agent is not fully enabled on the repo.

**Quality score is always low**
→ Check that `GH_PAT_TOKEN` has access to GitHub Models (`https://models.inference.ai.azure.com`). The token needs to belong to an account with GitHub Models access (available on free and paid plans).

---

## License

MIT
