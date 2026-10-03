"""Bounded, data-only repository inspection. Git refs resolve before any files are read."""

import os
import re
import stat
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

from .util import digest, now, redact

MAX_FILES = 5000
MAX_FILE = 256 * 1024
MAX_TOTAL = 10 * 1024 * 1024
MAX_SECONDS = 30
SKIP = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".agent-review",
    ".aws",
    ".ssh",
    ".codex",
    ".agents",
    ".claude",
}
TEXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".toml", ".json", ".yaml", ".yml", ".txt"}
CAPABILITIES = {
    "retrieval": r"\b(rag|retrieval|vector.?store)\b|检索",
    "memory": r"\b(memory|checkpoint)\b|记忆",
    "tools": r"\b(tool.?call|function.?call|mcp)\b|工具调用",
    "web": r"\b(brows(e|ing|er)|web.?search)\b|网页|联网搜索",
    "code": r"\b(code.?repair|coding|code.?generation)\b|代码修复|代码生成",
    "document": r"\b(pdf|ocr|document.?conversion)\b|文档转换",
    "multi_agent": r"\b(multi.?agent|a2a)\b|多智能体",
}


def _git(root: Path, *args: str, limit=MAX_FILE) -> bytes:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
    }
    argv = [
        "git",
        "--no-replace-objects",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        f"safe.directory={root}",
        "-C",
        str(root),
        *args,
    ]
    # stdout goes to a bounded reader; stderr is not surfaced (it can contain paths/credentials).
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env) as proc:
        try:
            # A subprocess timeout also bounds a stalled filesystem/object store.
            timer = threading.Timer(15, proc.kill)
            timer.start()
            try:
                content = proc.stdout.read(limit + 1)
                if len(content) > limit:
                    proc.kill()
                    raise ValueError("仓库 Git 输出超过检查上限。")
                if proc.wait(timeout=15):
                    raise ValueError("无法读取指定 Git 仓库或版本。")
                return content
            finally:
                timer.cancel()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()


def _snippet(line: str) -> str:
    line = redact(line)
    line = re.sub(
        r"(?i)((?:['\"]?\w*(?:token|secret|password|api_key|apikey)\w*['\"]?)\s*[:=]\s*)"
        r"(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;]+)",
        r"\1[redacted:secret]",
        line,
    )
    return line[:300]


def _read_snapshot(root: Path, name: str) -> bytes:
    """Open each path component without following symlinks, including raced parent directories."""
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parts = PurePosixPath(name).parts
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ValueError("快照源文件不是普通文件。")
            return source.read(MAX_FILE + 1)
    finally:
        os.close(directory)


def inspect_repository(path: Path, ref="HEAD", repository_url: str | None = None) -> dict:
    started = time.monotonic()

    def deadline():
        if time.monotonic() - started > MAX_SECONDS:
            raise ValueError("仓库扫描超过 30 秒上限。")

    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("仓库路径必须为目录。")
    if repository_url:
        url = urlsplit(repository_url)
        if (
            url.scheme != "https"
            or url.netloc != "github.com"
            or url.query
            or url.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", url.path)
        ):
            raise ValueError("源码链接需要无凭据的 https://github.com/owner/repo URL。")
        repository_url = repository_url.rstrip("/").removesuffix(".git")
    if not ref or ref.startswith("-") or len(ref) > 200:
        raise ValueError("无效的 Git ref。")
    is_git = (root / ".git").exists()
    commit = None
    skipped = []
    blobs = []
    if is_git:
        commit = _git(root, "rev-parse", "--verify", ref + "^{commit}", limit=128).decode().strip()
        if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
            raise ValueError("Git commit 格式无效。")
        tree = _git(root, "ls-tree", "-rlz", commit, limit=4 * 1024 * 1024)
        records = tree.split(b"\0")
        if len(records) - 1 > MAX_FILES:
            raise ValueError("仓库文件数量超过 5000；请先准备较小的评测仓库。")
        for record in records:
            if not record:
                continue
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, oid, size = meta.decode().split()
            try:
                name = raw_path.decode("utf-8")
            except UnicodeDecodeError:
                skipped.append({"path": "[non-UTF8 path]", "reason": "path_encoding"})
                continue
            if mode not in {"100644", "100755"} or kind != "blob":
                skipped.append({"path": name, "reason": "symlink_or_submodule"})
            else:
                blobs.append((name, int(size), oid))
    else:
        if ref != "HEAD":
            raise ValueError("非 Git 目录只支持内容快照，不能指定 ref。")
        for directory, dirs, names in os.walk(root, followlinks=False):
            deadline()
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(directory) / d).is_symlink())
            for name in sorted(names):
                item = Path(directory) / name
                relative = item.relative_to(root).as_posix()
                if item.is_symlink() or not item.is_file():
                    skipped.append({"path": relative, "reason": "symlink_or_special"})
                else:
                    blobs.append((relative, item.stat().st_size, None))
                if len(blobs) + len(skipped) > MAX_FILES:
                    raise ValueError("仓库文件数量超过 5000。")
    files, observations, claims = [], [], []
    total = 0
    for name, size, oid in sorted(blobs):
        deadline()
        item = PurePosixPath(name)
        if any(part in SKIP for part in item.parts) or item.is_absolute() or ".." in item.parts:
            continue
        if item.name.startswith(".env") and item.name != ".env.example":
            skipped.append({"path": name, "reason": "secret_file"})
            continue
        if item.suffix.lower() not in TEXT and item.name not in {"Dockerfile", ".env.example"}:
            continue
        if size > MAX_FILE or total + size > MAX_TOTAL:
            skipped.append({"path": name, "reason": "size_limit"})
            continue
        if oid:
            raw = _git(root, "cat-file", "blob", oid)
        else:
            raw = _read_snapshot(root, name)
            if len(raw) > MAX_FILE:
                skipped.append({"path": name, "reason": "size_changed"})
                continue
        total += len(raw)
        deadline()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append({"path": name, "reason": "binary"})
            continue
        lines = content.splitlines()
        files.append({"path": name, "sha256": digest(raw), "bytes": len(raw), "lines": len(lines)})
        for index, line in enumerate(lines, 1):
            evidence = {"path": name, "line": index, "snippet": _snippet(line)}
            if commit and repository_url:
                evidence["url"] = f"{repository_url}/blob/{commit}/{quote(name, safe='/')}#L{index}"
            if item.name.lower().startswith("readme"):
                for capability, pattern in CAPABILITIES.items():
                    if re.search(pattern, line, re.I):
                        claims.append(
                            {
                                "id": "claim_" + digest([name, index, capability])[:16],
                                "capability": capability,
                                "status": "unverified_claim",
                                **evidence,
                            }
                        )
            kinds = []
            if item.name in {"pyproject.toml", "requirements.txt", "package.json"} and re.search(
                r"(?i)(dependencies|scripts|\[project|fastapi|langchain|langgraph|openai|anthropic|a2a|mcp)",
                line,
            ):
                kinds.append("dependency_or_entrypoint")
            if re.search(r"(?i)(getenv\(|environ\[|environ\.get\(|process\.env\.|env::var\()", line):
                kinds.append("environment_requirement")
            if re.search(r"(?i)(FastAPI\(|uvicorn|app\.listen\(|ENTRYPOINT|CMD\s|EXPOSE\s)", line):
                kinds.append("service_entrypoint")
            if item.suffix in {".py", ".ts", ".js"} and re.search(
                r"(?i)(@.*tool|BaseTool|\.add_tool\(|@.*route|@app\.(get|post))", line
            ):
                kinds.append("tool_or_route")
            for kind in kinds:
                if len(observations) < 1000:
                    observations.append({"kind": kind, "status": "static_observation", **evidence})
            if len(claims) > 1000:
                raise ValueError("README 能力声明数量超过 1000。")
    source_hash = digest(files)
    body = {
        "profile_version": "agent-review/repository-v1",
        "commit": commit,
        "source_hash": source_hash,
        "repository_url": repository_url,
        "snapshot_kind": "git_commit" if commit else "content_snapshot",
        "files": files,
        "claims": claims,
        "observations": observations,
        "skipped": skipped,
        "limitations": [
            "静态声明和代码线索不证明运行能力。",
            "未执行仓库代码或 README 中的指令。",
            "扫描仅包含有限大小的文本文件；source_hash 覆盖实际扫描内容。",
            "Git 仓库只读取固定提交，忽略工作区未提交修改；内容快照不证明部署版本。",
        ],
    }
    return {"id": "repo_" + digest(body)[:20], "created_at": now(), **body}


def compare_repositories(left: dict, right: dict) -> dict:
    a = {f["path"]: f["sha256"] for f in left["files"]}
    b = {f["path"]: f["sha256"] for f in right["files"]}
    return {
        "left": left["id"],
        "right": right["id"],
        "added": sorted(b.keys() - a.keys()),
        "removed": sorted(a.keys() - b.keys()),
        "changed": sorted(p for p in a.keys() & b.keys() if a[p] != b[p]),
        "scope": "inspected_text_files",
    }
