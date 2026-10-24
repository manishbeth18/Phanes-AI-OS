
"""Conservative, rule-based planner for the first PHANES integration.

Only explicitly supported, read-only requests are accepted.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from uuid import uuid4

from shared.contracts import Plan, PlanStep


class UnsupportedRequestError(ValueError):
    """Raised when a request is outside the supported planner scope."""


class ReadOnlyPlanner:
    """Translate a small allowlist of requests into registered tools."""

    def __call__(self, goal: str, workspace: str, context: object) -> Plan:
        del workspace, context  # Reserved for future planner implementations.

        if not isinstance(goal, str) or not goal.strip():
            raise UnsupportedRequestError("Enter a task to continue.")

        text = goal.strip()
        lower = text.lower()
        step: PlanStep | None = None
        rationale = ""

        if re.search(r"\b(list|show|display)\b", lower) and re.search(
            r"\b(files|folders|directory|directories|workspace contents)\b",
            lower,
        ):
            step = PlanStep(
                step_id=f"list_workspace_{uuid4().hex}",
                tool="filesystem.list_directory",
                arguments={"path": "."},
                description="List files and folders in the selected workspace.",
            )
            rationale = "List the workspace contents without changing files."


        elif (
            re.search(
                r"\b(read|show|open|display|inspect|analy[sz]e|debug|"
                r"review|explain|find|identify|check)\b",
                lower,
            )
            and re.search(
                r"\b(file|code|bug|bugs|error|errors|issue|issues|"
                r"problem|problems|function|class|script)\b",
                lower,
            )
        ):
            # Only accept an explicitly named relative file path.
            # The filesystem tool must independently enforce workspace
            # confinement when it executes this read operation.
            path_match = re.search(
                r"""(?<![\w.-])((?:[\w.-]+[\\/])*[\w.-]+\.[A-Za-z0-9_+-]+)""",
                text,
            )

            if path_match:
                requested_path = path_match.group(1).replace("\\", "/")

                if (
                    requested_path.startswith("/")
                    or re.match(r"^[A-Za-z]:", requested_path)
                    or any(part == ".." for part in requested_path.split("/"))
                ):
                    raise UnsupportedRequestError(
                        "Please specify a file inside the selected workspace."
                    )

                step = PlanStep(
                    step_id=f"read_file_{uuid4().hex}",
                    tool="filesystem.read_file",
                    arguments={"path": requested_path},
                    description=f"Read {requested_path} for analysis without modifying it.",
                )
                rationale = (
                    "Read the requested workspace file so its actual contents "
                    "can be analyzed without changing the file."
                )
            else:
                raise UnsupportedRequestError(
                    "Please include the filename you want PHANES to inspect, "
                    "such as debug_demo.py."
                )

        elif "readme" in lower and re.search(
            r"\b(read|show|open|display|contents|content)\b", lower
        ):
            step = PlanStep(
                step_id=f"read_readme_{uuid4().hex}",
                tool="filesystem.read_file",
                arguments={"path": "README.md"},
                description="Read the workspace README.",
            )
            rationale = "Read the workspace README without modifying it."

        elif re.search(r"\b(git\s+status|status\s+of\s+(the\s+)?git|"
                       r"working tree|uncommitted changes)\b", lower):
            step = PlanStep(
                step_id=f"git_status_{uuid4().hex}",
                tool="git.status",
                description="Inspect the repository status.",
            )
            rationale = "Inspect Git status without modifying the repository."

        elif re.search(r"\b(current|active)\s+branch\b", lower) or (
            "git branch" in lower and "recent" not in lower
        ):
            step = PlanStep(
                step_id=f"git_branch_{uuid4().hex}",
                tool="git.current_branch",
                description="Identify the current Git branch.",
            )
            rationale = "Read the current branch name."

        elif re.search(r"\b(recent|latest)\s+(git\s+)?commits\b", lower):
            step = PlanStep(
                step_id=f"recent_commits_{uuid4().hex}",
                tool="git.recent_commits",
                arguments={"limit": 5},
                description="Show the five most recent commits.",
            )
            rationale = "Read recent commit history."

        else:
            url_match = re.search(r"https?://[^\s<>\"']+", text)
            if url_match and re.search(
                r"\b(fetch|retrieve|read|inspect|summarize|summary|"
                r"open|visit)\b",
                lower,
            ):
                url = url_match.group(0).rstrip(".,);]")
                self._validate_public_url(url)
                step = PlanStep(
                    step_id=f"fetch_public_page_{uuid4().hex}",
                    tool="browser.fetch_page",
                    arguments={"url": url},
                    description=f"Fetch the public webpage at {url}.",
                )
                rationale = "Fetch a public webpage without interacting with it."

        if step is None:
            raise UnsupportedRequestError(
                "I can't safely plan that request yet. Try asking to list "
                "workspace files, read the README, check Git status, identify "
                "the current branch, show recent commits, or fetch a public "
                "HTTP(S) webpage."
            )

        # The service generates the real task ID. It normalizes this placeholder
        # to that ID immediately after the planner returns.
        return Plan(
            task_id="planner-generated",
            steps=[step],
            rationale=rationale,
        )

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" and parsed.scheme != "http":
            raise UnsupportedRequestError("Only HTTP(S) webpages are supported.")

        if not parsed.hostname or parsed.username or parsed.password:
            raise UnsupportedRequestError("That URL is not supported.")

        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
            raise UnsupportedRequestError("Local and private webpages are not supported.")

        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None

        if address is not None and (
            not address.is_global
            or address.is_multicast
            or address.is_unspecified
        ):
            raise UnsupportedRequestError("Private or non-public IP addresses are not supported.")
