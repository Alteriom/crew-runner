"""CrewAI BaseTool wrappers for the crew-runner sidecar.

Counterpart: crewai-backend/src/tools/crewai_wrappers.py (backend version
uses direct async service tools; this version uses MCP/httpx calls since
the crew-runner is an isolated Docker container that can't import backend modules).

Each wrapper bridges tool types to CrewAI's synchronous BaseTool interface.

Tool types handled:
  ssh         → SSHCrewAITool
  github_api  → GitHubCrewAITool
  docker      → DockerCrewAITool
  filesystem  → FilesystemCrewAITool
  http        → HTTPCrewAITool
  api         → HTTPCrewAITool (alias)
  custom      → HTTPCrewAITool (generic fallback)
  web_search  → WebSearchCrewAITool
"""
import json
import logging
from typing import Any, Dict, Optional, Type

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSH Tool
# ---------------------------------------------------------------------------

class SSHToolInput(BaseModel):
    host: str = Field(description="Remote server name or IP (e.g. 'friday', 'jarvis', '158.69.197.200')")
    command: str = Field(description="Shell command to execute on the remote server")


class SSHCrewAITool(BaseTool):
    name: str = "ssh_executor"
    description: str = (
        "Execute shell commands on remote Alteriom servers via mcp-server-infra. "
        "Servers available: friday, jarvis, iris, northrelay, mailbox, pool-gateway, sovereign. "
        "Use server names not raw IPs."
    )
    args_schema: Type[BaseModel] = SSHToolInput
    credentials: Dict[str, Any] = {}

    def _run(self, host: str, command: str) -> str:
        """Call mcp-server-infra server_exec via centralised MCP helper."""
        return _call_mcp_tool("server_exec", {"server": host, "command": command}, timeout=60.0)


# ---------------------------------------------------------------------------
# GitHub API Tool
# ---------------------------------------------------------------------------

class GitHubToolInput(BaseModel):
    action: str = Field(description="Action: get_repo, list_issues, get_issue, list_prs, get_pr, create_issue")
    owner: str = Field(description="Repository owner")
    repo: str = Field(description="Repository name")
    extra: str = Field(default="", description="Extra params as JSON (e.g. issue_number, pr_number, title, body)")


class GitHubCrewAITool(BaseTool):
    name: str = "github_api"
    description: str = (
        "Interact with the GitHub REST API. "
        "Actions: get_repo, list_issues, get_issue, list_prs, get_pr, create_issue. "
        "Pass extra params as JSON in the 'extra' field."
    )
    args_schema: Type[BaseModel] = GitHubToolInput
    credentials: Dict[str, Any] = {}

    def _run(self, action: str, owner: str, repo: str, extra: str = "") -> str:
        import os, httpx
        token = self.credentials.get("token") or os.environ.get("GITHUB_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        base = "https://api.github.com"
        extra_data = json.loads(extra) if extra else {}

        try:
            if action == "get_repo":
                resp = httpx.get(f"{base}/repos/{owner}/{repo}", headers=headers, timeout=30)
            elif action == "list_issues":
                resp = httpx.get(f"{base}/repos/{owner}/{repo}/issues", headers=headers, timeout=30,
                                 params={"state": extra_data.get("state","open"), "per_page": extra_data.get("per_page",30)})
            elif action == "get_issue":
                resp = httpx.get(f"{base}/repos/{owner}/{repo}/issues/{extra_data.get('issue_number',1)}", headers=headers, timeout=30)
            elif action == "list_prs":
                resp = httpx.get(f"{base}/repos/{owner}/{repo}/pulls", headers=headers, timeout=30,
                                 params={"state": extra_data.get("state","open"), "per_page": extra_data.get("per_page",30)})
            elif action == "get_pr":
                resp = httpx.get(f"{base}/repos/{owner}/{repo}/pulls/{extra_data.get('pr_number',1)}", headers=headers, timeout=30)
            elif action == "create_issue":
                resp = httpx.post(f"{base}/repos/{owner}/{repo}/issues", headers=headers, timeout=30,
                                  json={"title": extra_data.get("title",""), "body": extra_data.get("body",""), "labels": extra_data.get("labels")})
            elif action == "get_file":
                resp = httpx.get(f"{base}/repos/{owner}/{repo}/contents/{extra_data.get('path','')}", headers=headers, timeout=30)
                if resp.status_code == 200:
                    import base64 as b64
                    content = resp.json().get("content","")
                    return b64.b64decode(content).decode("utf-8", errors="replace")[:8000]
            else:
                return f"Unknown action: {action}. Use: get_repo, list_issues, get_issue, list_prs, get_pr, create_issue, get_file"

            resp.raise_for_status()
            result = resp.json()
            output = json.dumps(result, indent=2, default=str)
            if len(output) > 10000:
                return output[:9900] + f"\n\n... [truncated, {len(output)} chars total]"
            return output
        except Exception as exc:
            return f"GitHub API error: {exc}"


# ---------------------------------------------------------------------------
# Docker Tool
# ---------------------------------------------------------------------------

class DockerToolInput(BaseModel):
    host: str = Field(
        default="friday",
        description="Server name where docker runs (e.g. 'friday', 'jarvis')"
    )
    docker_command: str = Field(
        description="Docker command without 'docker' prefix (e.g. 'ps -a', 'logs command-center-backend --tail 50')"
    )


class DockerCrewAITool(BaseTool):
    name: str = "docker_manager"
    description: str = (
        "Run Docker commands on Alteriom servers via mcp-server-infra. "
        "Specify the server name and docker command (without 'docker' prefix). "
        "Examples: host=friday command='ps -a', host=friday command='logs command-center-backend --tail 50'"
    )
    args_schema: Type[BaseModel] = DockerToolInput
    credentials: Dict[str, Any] = {}

    def _run(self, host: str = "friday", docker_command: str = "ps") -> str:
        """Call mcp-server-infra server_exec with docker prefix via centralised helper."""
        return _call_mcp_tool("server_exec", {"server": host, "command": f"docker {docker_command}"}, timeout=60.0)


# ---------------------------------------------------------------------------
# Filesystem Tool
# ---------------------------------------------------------------------------

class FilesystemToolInput(BaseModel):
    action: str = Field(description="Action: read_file, write_file, create_dir, delete_file, list_dir, stat")
    path: str = Field(description="Absolute path on the remote server")
    content: str = Field(default="", description="File content (required for write_file)")
    append: bool = Field(default=False, description="Append to file instead of overwrite (write_file only)")


class FilesystemCrewAITool(BaseTool):
    name: str = "filesystem_tool"
    description: str = (
        "Remote filesystem operations via SSH. "
        "Actions: read_file, write_file, create_dir, delete_file, list_dir, stat. "
        "write_file: write content to a file (creates parent dirs automatically). "
        "create_dir: mkdir -p. delete_file: rm -f (files only)."
    )
    args_schema: Type[BaseModel] = FilesystemToolInput
    credentials: Dict[str, Any] = {}

    def _run(self, action: str, path: str, content: str = "", append: bool = False) -> str:
        """Use mcp-server-infra server_exec for filesystem ops on remote servers."""
        import base64 as _b64
        import shlex as _shlex
        server = self.credentials.get("host", "friday")

        if action == "read_file":
            command = f"cat {_shlex.quote(path)}"
        elif action == "write_file":
            b64 = _b64.b64encode(content.encode("utf-8")).decode("ascii")
            op = ">>" if append else ">"
            command = (
                f"mkdir -p $(dirname {_shlex.quote(path)}) && "
                f"echo {_shlex.quote(b64)} | base64 -d {op} {_shlex.quote(path)}"
            )
        elif action == "create_dir":
            command = f"mkdir -p {_shlex.quote(path)}"
        elif action == "delete_file":
            command = f"rm -f {_shlex.quote(path)}"
        elif action == "list_dir":
            command = f"ls -1 {_shlex.quote(path)}"
        elif action == "stat":
            command = f"stat {_shlex.quote(path)}"
        else:
            return f"Unknown action: {action}. Use: read_file, write_file, create_dir, delete_file, list_dir, stat"

        return _call_mcp_tool("server_exec", {"server": server, "command": command}, timeout=60.0)


# ---------------------------------------------------------------------------
# HTTP Tool
# ---------------------------------------------------------------------------

class HTTPToolInput(BaseModel):
    method: str = Field(default="GET", description="HTTP method: GET or POST")
    url: str = Field(description="Full URL to request")
    body: str = Field(default="", description="Request body as JSON string (for POST)")


class HTTPCrewAITool(BaseTool):
    name: str = "http_client"
    description: str = "Make HTTP requests (GET/POST) and return the response."
    args_schema: Type[BaseModel] = HTTPToolInput
    credentials: Dict[str, Any] = {}

    def _run(self, url: str, method: str = "GET", body: str = "") -> str:
        import httpx
        try:
            parsed_body = json.loads(body) if body else None
            headers = {"Content-Type": "application/json"} if parsed_body else {}
            if method.upper() == "POST":
                resp = httpx.post(url, json=parsed_body, headers=headers, timeout=15, follow_redirects=True)
            else:
                resp = httpx.get(url, timeout=15, follow_redirects=True)
            try:
                data = resp.json()
            except Exception:
                data = resp.text[:5000]
            return json.dumps({"status_code": resp.status_code, "data": data}, default=str)[:10000]
        except Exception as exc:
            return f"HTTP error: {exc}"


# ---------------------------------------------------------------------------
# Web Search Tool
# ---------------------------------------------------------------------------

class WebSearchToolInput(BaseModel):
    query: str = Field(description="Search query")
    max_results: int = Field(default=10, description="Maximum number of results (1-20)")


class WebSearchCrewAITool(BaseTool):
    name: str = "web_search"
    description: str = "Search the web using DuckDuckGo. Returns titles, URLs, and snippets."
    args_schema: Type[BaseModel] = WebSearchToolInput
    credentials: Dict[str, Any] = {}

    def _run(self, query: str, max_results: int = 10) -> str:
        import httpx, urllib.parse
        try:
            params = {"q": query, "format": "json", "no_redirect": 1, "no_html": 1}
            resp = httpx.get("https://api.duckduckgo.com/", params=params, timeout=15, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
            data = resp.json()
            results = data.get("RelatedTopics", [])[:max_results]
            lines = []
            for r in results:
                if "Text" in r and "FirstURL" in r:
                    lines.append(f"- {r['Text']}\n  {r['FirstURL']}")
            if not lines:
                # Fallback: return abstract
                abstract = data.get("Abstract", "")
                return abstract if abstract else f"No results for: {query}"
            return "\n\n".join(lines)
        except Exception as exc:
            return f"Search error: {exc}"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Git Tool
# ---------------------------------------------------------------------------

class GitToolInput(BaseModel):
    action: str = Field(
        description="Action: checkout, create_branch, add, commit, push, status, log, diff, create_pr"
    )
    repo_path: str = Field(
        default="/home/ubuntu/workspaces/alteriom-dev-ops",
        description="Absolute path to the git repository on the remote server",
    )
    branch: str = Field(default="", description="Branch name (checkout, create_branch, push)")
    message: str = Field(default="", description="Commit message (commit)")
    files: str = Field(default=".", description="Files to add (add), '.' for all")
    pr_title: str = Field(default="", description="PR title (create_pr)")
    pr_body: str = Field(default="", description="PR body (create_pr)")
    base_branch: str = Field(default="main", description="Base branch for PR (create_pr)")


class GitCrewAITool(BaseTool):
    name: str = "git_tool"
    description: str = (
        "Git operations on a remote server via SSH (MCP). "
        "Actions: checkout, create_branch, add, commit, push, status, log, diff, create_pr."
    )
    args_schema: Type[BaseModel] = GitToolInput
    credentials: Dict[str, Any] = {}

    def _run(
        self,
        action: str,
        repo_path: str = "/home/ubuntu/workspaces/alteriom-dev-ops",
        branch: str = "",
        message: str = "",
        files: str = ".",
        pr_title: str = "",
        pr_body: str = "",
        base_branch: str = "main",
    ) -> str:
        import shlex as _shlex
        server = self.credentials.get("host", "friday")

        def git(cmd: str) -> str:
            full_cmd = f"cd {_shlex.quote(repo_path)} && {cmd}"
            return _call_mcp_tool("server_exec", {"server": server, "command": full_cmd}, timeout=120.0)

        if action == "checkout":
            if not branch:
                return "Error: branch is required for checkout"
            return git(f"git checkout {_shlex.quote(branch)}")
        elif action == "create_branch":
            if not branch:
                return "Error: branch is required for create_branch"
            return git(
                f"git checkout main && git pull -q && git checkout -b {_shlex.quote(branch)}"
            )
        elif action == "add":
            return git(f"git add {files}")
        elif action == "commit":
            if not message:
                return "Error: message is required for commit"
            return git(f"git commit -m {_shlex.quote(message)}")
        elif action == "push":
            branch_arg = f" origin {_shlex.quote(branch)}" if branch else ""
            return git(f"git push{branch_arg}")
        elif action == "status":
            return git("git status --short")
        elif action == "log":
            return git("git log --oneline -10")
        elif action == "diff":
            return git("git diff HEAD --stat")
        elif action == "create_pr":
            if not pr_title:
                return "Error: pr_title is required for create_pr"
            current_branch = git("git rev-parse --abbrev-ref HEAD")
            if "Error" in current_branch:
                return current_branch
            current_branch = current_branch.strip()
            cmd = (
                f"gh pr create --repo Alteriom/alteriom-dev-ops "
                f"--title {_shlex.quote(pr_title)} "
                f"--body {_shlex.quote(pr_body or pr_title)} "
                f"--base {_shlex.quote(base_branch)} "
                f"--head {_shlex.quote(current_branch)}"
            )
            return git(cmd)
        else:
            return (
                f"Unknown action: {action}. "
                "Use: checkout, create_branch, add, commit, push, status, log, diff, create_pr"
            )


# Maps DB tool type → CrewAI wrapper class
_TOOL_TYPE_MAP: Dict[str, Type[BaseTool]] = {
    "ssh": SSHCrewAITool,
    "github_api": GitHubCrewAITool,
    "docker": DockerCrewAITool,
    "filesystem": FilesystemCrewAITool,
    "http": HTTPCrewAITool,
    "api": HTTPCrewAITool,
    "web_search": WebSearchCrewAITool,
    "git": GitCrewAITool,
}

# Maps DB tool name → CrewAI wrapper class (fallback when type is generic)
_TOOL_NAME_MAP: Dict[str, Type[BaseTool]] = {
    "ssh-executor": SSHCrewAITool,
    "github-api": GitHubCrewAITool,
    "docker-manager": DockerCrewAITool,
    "filesystem-tool": FilesystemCrewAITool,
    "git-tool": GitCrewAITool,
    "http-client": HTTPCrewAITool,
    "web-search": WebSearchCrewAITool,
}


def create_crewai_tool(
    tool_type: str,
    tool_name: str,
    credentials: Optional[Dict[str, Any]] = None,
    description: Optional[str] = None,
) -> Optional[BaseTool]:
    """Create a CrewAI tool instance from DB tool metadata.

    Resolves the wrapper class by type first, then by name as fallback.
    Returns None if the tool type/name is not supported.
    """
    wrapper_cls = _TOOL_TYPE_MAP.get(tool_type) or _TOOL_NAME_MAP.get(tool_name)
    if wrapper_cls is None:
        logger.debug("create_crewai_tool: no wrapper for type=%r name=%r", tool_type, tool_name)
        return None

    tool = wrapper_cls(credentials=credentials or {})
    if description:
        tool.description = description
    logger.debug("create_crewai_tool: created %s for type=%r name=%r", wrapper_cls.__name__, tool_type, tool_name)
    return tool


def _call_mcp_tool(tool_name: str, arguments: dict, timeout: float = 30.0) -> str:
    """Call mcp-server-infra via HTTP MCP and parse SSE response.
    
    Centralised helper used by all infrastructure tools.
    """
    import os, httpx, json as _json

    mcp_url = os.environ.get("INFRA_MCP_URL", "http://172.18.0.1:3001")
    mcp_token = os.environ.get("INFRA_MCP_TOKEN", "")
    if not mcp_token:
        return "MCP error: INFRA_MCP_TOKEN env var not set — cannot call infrastructure tools"

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "id": 1,
        "params": {"name": tool_name, "arguments": arguments}
    }
    try:
        resp = httpx.post(
            f"{mcp_url}/mcp",
            json=payload,
            headers={
                "Authorization": f"Bearer {mcp_token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            timeout=timeout,
        )
        resp.raise_for_status()

        # Parse SSE: look for 'data: {...}' lines
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                try:
                    data = _json.loads(line[6:])
                    content = data.get("result", {}).get("content", [])
                    if content:
                        # For server_exec, content[0].text is a JSON object with per-server results
                        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
                        try:
                            parsed = _json.loads(text)
                            # server_exec returns {server_name: {stdout, stderr, exitCode}}
                            for server, result in parsed.items():
                                if result.get("exitCode", 0) != 0:
                                    return f"Exit {result['exitCode']}\nstderr: {result.get('stderr', '')}\nstdout: {result.get('stdout', '')}"
                                return result.get("stdout", "")
                        except Exception:
                            return text
                    err = data.get("error", {})
                    if err:
                        return f"MCP error: {err.get('message', str(err))}"
                except Exception:
                    pass
        return f"No data in response"
    except Exception as exc:
        return f"MCP tool error ({tool_name}): {exc}"
