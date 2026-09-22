"""Git 提交采集模块。

仿照 kang-skills/weekly-report 的思路：扫描 scanDir 下所有 git 仓库，
按当前 git 用户与目标周日期范围收集提交，作为周报生成的证据底料。

配置来源（优先级从高到低）：
1. 环境变量 WEEKLY_GIT_SCAN_DIR / WEEKLY_GIT_AUTHOR / WEEKLY_GIT_MAX_DEPTH
2. 项目根目录 config.json 的 git 字段（不提交到 Git）
3. 内置默认值

author 支持正则（例如 "zhangsan|zhangsan@company.com"），
留空时自动读取 git 全局配置的 user.name / user.email。
"""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_EXCLUDE_DIRS = [
    "node_modules",
    ".venv",
    "venv",
    "myenv",
    "weekly-agent-env",
    "__pycache__",
    "dist",
    "build",
    ".cache",
    ".tox",
    "site-packages",
    ".trash",
]


@dataclass(frozen=True)
class GitScanConfig:
    scan_dir: str = ""
    max_depth: int = 3
    author: str = ""
    exclude_dirs: tuple[str, ...] = tuple(DEFAULT_EXCLUDE_DIRS)


def load_git_scan_config() -> GitScanConfig:
    data: dict[str, Any] = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get("git", {})
        except (OSError, ValueError):
            data = {}

    scan_dir = (os.getenv("WEEKLY_GIT_SCAN_DIR") or str(data.get("scan_dir") or "")).strip()
    author = (os.getenv("WEEKLY_GIT_AUTHOR") or str(data.get("author") or "")).strip()
    try:
        max_depth = int(os.getenv("WEEKLY_GIT_MAX_DEPTH") or data.get("max_depth") or 3)
    except (TypeError, ValueError):
        max_depth = 3
    if max_depth < 1:
        max_depth = 1

    exclude_dirs = data.get("exclude_dirs") or DEFAULT_EXCLUDE_DIRS
    return GitScanConfig(
        scan_dir=scan_dir,
        max_depth=max_depth,
        author=author,
        exclude_dirs=tuple(str(item) for item in exclude_dirs),
    )


def run_git(args: list[str], cwd: Path | None = None, timeout: float = 15.0) -> str | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd or BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return proc.stdout


def discover_repositories(
    scan_dir: str,
    max_depth: int = 3,
    exclude_dirs: tuple[str, ...] = (),
) -> list[Path]:
    root = Path(scan_dir)
    if not root.is_dir():
        return []

    repos: list[Path] = []

    if (root / ".git").exists():
        repos.append(root)

    def walk(directory: Path, depth: int) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            return
        for entry in entries:
            if entry.name in exclude_dirs or not entry.is_dir():
                continue
            if (entry / ".git").exists():
                repos.append(entry)
            else:
                walk(entry, depth + 1)

    walk(root, 0)
    return repos


def detect_git_author() -> str:
    for scope in ("--global", "--local"):
        value = run_git(["config", scope, "user.name"])
        if value and value.strip():
            return value.strip()

    value = run_git(["config", "--global", "user.email"])
    if value and value.strip():
        return value.strip()
    return ""


def collect_repo_commits(
    repo: Path,
    author: str,
    start: str,
    end: str,
) -> list[dict[str, str]]:
    try:
        end_exclusive = (
            datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
        ).date().isoformat()
    except ValueError:
        return []

    args = [
        "log",
        "--all",
        "--no-merges",
        f"--since={start} 00:00:00",
        f"--until={end_exclusive} 00:00:00",
        "--date=format:%Y-%m-%d %H:%M",
        "--pretty=format:%h|%ad|%s",
        "-n",
        "500",
    ]
    if author:
        args.append(f"--author={author}")

    output = run_git(["-C", str(repo), *args])
    if not output or not output.strip():
        return []

    commits: list[dict[str, str]] = []
    for line in output.strip().splitlines():
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        short_hash, committed_at, subject = (part.strip() for part in parts)
        if not short_hash or not subject:
            continue
        commits.append(
            {
                "hash": short_hash,
                "date": committed_at,
                "message": subject,
            }
        )
    return commits


def collect_weekly_commits(
    start: str,
    end: str,
    scan_dir: str | None = None,
    author: str | None = None,
) -> dict[str, Any]:
    config = load_git_scan_config()
    resolved_scan_dir = (scan_dir or config.scan_dir).strip()
    resolved_author = (author or config.author).strip()

    if not resolved_scan_dir:
        return {
            "configured": False,
            "message": "未配置 git 扫描目录。请在 config.json 的 git.scan_dir 或环境变量 WEEKLY_GIT_SCAN_DIR 中指定仓库根目录。",
            "repos": [],
            "total_commits": 0,
        }

    if not resolved_author:
        resolved_author = detect_git_author()

    repos = discover_repositories(
        resolved_scan_dir, config.max_depth, config.exclude_dirs
    )

    repo_results: list[dict[str, Any]] = []
    total_commits = 0
    for repo in repos:
        commits = collect_repo_commits(repo, resolved_author, start, end)
        if not commits:
            continue
        total_commits += len(commits)
        repo_results.append(
            {
                "repo": repo.name,
                "path": str(repo),
                "commit_count": len(commits),
                "commits": commits,
            }
        )

    return {
        "configured": True,
        "scan_dir": resolved_scan_dir,
        "author": resolved_author or "(全部作者)",
        "scanned_repos": len(repos),
        "active_repos": len(repo_results),
        "start": start,
        "end": end,
        "total_commits": total_commits,
        "repos": repo_results,
    }


def render_git_commits_text(git_data: dict[str, Any]) -> str:
    if not git_data.get("configured"):
        return ""

    if not git_data.get("repos"):
        return f"Git 提交：{git_data.get('start')} 至 {git_data.get('end')} 未发现提交记录。"

    lines: list[str] = []
    for repo in git_data["repos"]:
        lines.append(f"- [{repo['repo']}]（{repo['commit_count']} 次提交）")
        for commit in repo["commits"]:
            lines.append(f"  - {commit['date']} {commit['message']}")

    return "Git 提交记录：\n" + "\n".join(lines)
