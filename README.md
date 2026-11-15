# GitHub Code RAG — Project Guide

This guide explains how the GitHub repository RAG project ingests a public repository, retrieves relevant source code and documentation, reranks results, and answers with exact source citations.

## What the app does

Give the ingestion script a public GitHub repository URL. It shallow-clones the default branch, chunks supported text files, embeds and stores those chunks locally in Chroma, then lets an ADK agent answer questions about the indexed snapshot.

Answers can include retrieved code snippets and commit-pinned citations such as:

```text
https://github.com/owner/repo/blob/<commit-sha>/path/to/file.py#L20-L42
```

## Project layout

```text
GitHub Code RAG/
└── github_rag/
    ├── __init__.py
    ├── agent.py              # ADK agent, repository search, and LLM reranker
    ├── ingest.py             # Clone, filter, chunk, and index the repository
    ├── .env                  # Local Bedrock credentials/settings
    ├── source_repo/          # Shallow clone of the selected public repository
    ├── chroma_db/            # Persistent local vector database
    └── repo_state.json       # Repository URL and last indexed commit SHA
```

The local `.env` file contains credentials; keep it private and never paste its contents into source files or documentation.

## How ingestion works

`github_rag/ingest.py` takes a repository URL:

1. It checks the remote default-branch commit with `git ls-remote`.
2. If the same URL and commit are already indexed, it skips the clone and re-index.
3. If the commit changed, it makes a shallow clone with `git clone --depth 1`.
4. It filters out `.git`, tests, dependency/build folders, symlinks, lockfiles, binaries, and files larger than 500 KB. It keeps supported source, documentation, and configuration files.
5. For file types in `LANGUAGES`, Chonkie's `CodeChunker` uses Tree-sitter to split code and Markdown into structure-aware chunks. It uses a character tokenizer and a 900-character chunk size. The returned character offsets are converted to original line numbers.
6. For supported plain-text files without a mapped Tree-sitter grammar, it makes 30-line chunks with five lines of overlap.
7. Chroma creates local embeddings and persists the chunks. The script upserts 500 chunks at a time and prints progress. Once indexing completes, it saves the URL and commit SHA to `repo_state.json`.

The collection is rebuilt when the indexed commit changes, so removed or renamed files do not remain as stale results. If indexing stops before completion, `repo_state.json` is not updated; rerun the command to rebuild.

## How a question is answered

```text
User question
    ↓
ADK github_repo_agent
    ↓
Chroma vector search: top 20 candidate chunks
    ↓
GPT-6 Luna LLM reranker: order candidates by relevance
    ↓
Top 5 source-linked snippets
    ↓
GPT-6 Luna answer with exact code and citations
```

The repository search tool retrieves the top 20 candidates from Chroma. The reranker sends the query and candidates to the same Bedrock GPT-6 Luna model and asks it to return candidate numbers in relevance order. The tool returns the top five chunks with their language, file path, original line range, commit SHA, and a GitHub permalink. The ADK agent is instructed to use only retrieved results and quote code without rewriting it.

The reranker is an LLM-based reranker, not a dedicated cross-encoder. It adds one model call per question, which increases latency and model usage.

## Main files

### `github_rag/ingest.py`

- `get_remote_commit()` checks whether the repository has changed.
- `split_file()` uses Tree-sitter-backed chunks for mapped code/Markdown files and line chunks for plain text.
- `main()` clones, filters, chunks, and batches Chroma upserts.
- The file and embedding progress logs show file percentage, chunk counts, and embedding/storage percentage.

### `github_rag/agent.py`

- `search_repository()` retrieves up to 20 chunks and formats source metadata and commit-pinned links.
- `rerank_candidates()` asks the Bedrock model to rank candidate IDs. `reasoning_effort="none"` allows the configured `temperature=0` for this model.
- `root_agent` is the ADK agent registered for the app. It uses the LiteLLM connector with `bedrock/global.openai.gpt-6-luna`.

## Run or refresh the repository index

Activate the shared environment and run from the project root:

```bash
cd "/Documents/Rag Projects/GitHub Code RAG"
source "/Documents/Rag Projects/Document RAG/Docenv/bin/activate"
python github_rag/ingest.py https://github.com/google/adk-python
```

Replace the URL with another public repository's root URL. To refresh later, run the same command again. If the commit is unchanged, the script reports that it is already indexed; if it changed, it rebuilds the collection from the new snapshot.

## Run the chat UI

From the `GitHub Code RAG` project root, with `Docenv` activated:

```bash
adk web --port 8000
```

Open `http://localhost:8000` and select **`github_rag`**. ADK Web is the local development interface.

## Bedrock configuration

The local `github_rag/.env` file needs a Bedrock bearer token and region:

```text
AWS_BEARER_TOKEN_BEDROCK="your-token"
AWS_REGION_NAME="ap-south-1"
```

Never place the actual token in this guide. Short-term Bedrock keys expire; replace them locally when needed.

## Useful test questions

- “Where is `LlmAgent` defined? Show the exact code and cite the file lines.”
- “How does this repository route workflow nodes?”
- “Find the implementation of a specific function or class and explain its call path.”

For each answer, inspect the retrieval/tool result in ADK Web’s Events or Traces, open the GitHub citation, and verify the snippet against the indexed commit.

## Resources used

- [Google ADK Python repository](https://github.com/google/adk-python) — agent framework and source project indexed in this run.
- [ADK Python quickstart](https://adk.dev/get-started/python/) — agent package structure and local `adk web` workflow.
- [ADK LiteLLM model connector](https://adk.dev/agents/models/litellm/) — connecting the ADK agent to Bedrock through LiteLLM.
- [Chonkie CodeChunker](https://docs.chonkie.ai/oss/chunkers/code-chunker) — Tree-sitter-backed code chunking and source offsets.
- [Tree-sitter](https://tree-sitter.github.io/tree-sitter/) — concrete syntax tree parser used by the code chunker.
- [Chroma embedding functions](https://docs.trychroma.com/docs/embeddings/embedding-functions) — local default embeddings and vector retrieval.
- [Chroma clients](https://docs.trychroma.com/docs/run-chroma/clients) — persistent local Chroma storage.
- [Amazon Bedrock API keys](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) — Bedrock bearer API keys.
- [Amazon Bedrock inference profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html) — profile IDs and regional availability.
