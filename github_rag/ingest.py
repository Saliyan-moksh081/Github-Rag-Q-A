import json
import shutil
import subprocess
import sys
from pathlib import Path

import chromadb
from chonkie import CodeChunker


PACKAGE_DIR = Path(__file__).parent
REPO_DIR = PACKAGE_DIR / "source_repo"
DB_DIR = PACKAGE_DIR / "chroma_db"
STATE_FILE = PACKAGE_DIR / "repo_state.json"

SKIP_DIRS = {".git", "node_modules", "vendor", "dist", "build", ".venv","tests"}
SKIP_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock"}
MAX_FILE_SIZE = 500_000

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".sh": "bash",
    ".md": "markdown",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".css": "css",
    ".xml": "xml",
}

TEXT_EXTENSIONS = {".txt", ".rst", ".toml", ".ini", ".cfg"}


def get_remote_commit(repo_url):
    result = subprocess.run(
        ["git", "ls-remote", repo_url, "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.split()[0]


def split_file(path, repo_url, commit_sha):
    text = path.read_text(encoding="utf-8", errors="ignore")
    if not text.strip() or "\0" in text:
        return []

    relative_path = path.relative_to(REPO_DIR).as_posix()
    language = LANGUAGES.get(path.suffix.lower())
    chunks = []

    if language:
        code_chunks = CodeChunker(
            language=language,
            tokenizer="character",
            chunk_size=900,
        ).chunk(text)

        for part in code_chunks:
            start = part.start_index
            end = max(start, part.end_index - 1)
            chunks.append({
                "text": part.text,
                "repo_url": repo_url,
                "commit_sha": commit_sha,
                "path": relative_path,
                "start_line": text.count("\n", 0, start) + 1,
                "end_line": text.count("\n", 0, end) + 1,
                "language": language,
            })
    else:
        lines = text.splitlines()
        start = 0

        while start < len(lines):
            end = min(start + 30, len(lines))
            chunks.append({
                "text": "\n".join(lines[start:end]),
                "repo_url": repo_url,
                "commit_sha": commit_sha,
                "path": relative_path,
                "start_line": start + 1,
                "end_line": end,
                "language": "text",
            })

            if end == len(lines):
                break
            start = end - 5

    return chunks


def main(repo_url):
    repo_url = repo_url.rstrip("/")
    remote_sha = get_remote_commit(repo_url)

    client = chromadb.PersistentClient(path=str(DB_DIR))
    collection = client.get_or_create_collection("repository")

    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
        if (
            state.get("repo_url") == repo_url
            and state.get("commit_sha") == remote_sha
            and collection.count() > 0
        ):
            print(f"Repository is already indexed at {remote_sha[:8]}.")
            return

    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)

    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(REPO_DIR)],
        check=True,
    )
    commit_sha = subprocess.run(
        ["git", "-C", str(REPO_DIR), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    files = []

    for path in sorted(REPO_DIR.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_DIR).parts):
            continue
        if path.name in SKIP_FILES or path.stat().st_size > MAX_FILE_SIZE:
            continue
        if (
            path.suffix.lower() not in LANGUAGES
            and path.suffix.lower() not in TEXT_EXTENSIONS
            and path.name not in {"README", "LICENSE", "NOTICE"}
        ):
            continue

        files.append(path)

    chunks = []
    total_files = len(files)

    for number, path in enumerate(files, start=1):
        file_chunks = split_file(path, repo_url, commit_sha)
        chunks.extend(file_chunks)

        percent = number / total_files * 100
        relative_path = path.relative_to(REPO_DIR).as_posix()
        print(
            f"[{percent:5.1f}%] {number}/{total_files} files | "
            f"{relative_path} | {len(file_chunks)} chunks "
            f"({len(chunks)} total)",
            flush=True,
        )

    if not chunks:
        print("No supported text or code files found.")
        return

    old_ids = collection.get()["ids"]
    if old_ids:
        collection.delete(ids=old_ids)

    batch_size = 500
    total_chunks = len(chunks)

    for start in range(0, total_chunks, batch_size):
        batch = chunks[start:start + batch_size]

        collection.upsert(
            ids=[
                f"{chunk['path']}:{i}"
                for i, chunk in enumerate(batch, start=start)
            ],
            documents=[chunk["text"] for chunk in batch],
            metadatas=[
                {key: value for key, value in chunk.items() if key != "text"}
                for chunk in batch
            ],
        )

        processed = start + len(batch)
        print(
            f"Embedding/storing: {processed}/{total_chunks} chunks "
            f"({processed / total_chunks:.1%})",
            flush=True,
        )

    STATE_FILE.write_text(json.dumps({
        "repo_url": repo_url,
        "commit_sha": commit_sha,
    }, indent=2))

    print(f"Indexed {len(chunks)} chunks from commit {commit_sha[:8]}.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python github_rag/ingest.py <public-github-repo-url>")
        raise SystemExit(1)

    main(sys.argv[1])
