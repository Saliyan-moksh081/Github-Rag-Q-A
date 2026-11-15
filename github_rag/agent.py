from pathlib import Path
from urllib.parse import quote

import chromadb
from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm
import re
from litellm import completion

MODEL_ID = "bedrock/global.openai.gpt-6-luna"
DB_DIR = Path(__file__).parent / "chroma_db"

client = chromadb.PersistentClient(path=str(DB_DIR))
collection = client.get_collection("repository")


def rerank_candidates(query, documents, metadatas):
    candidates = "\n\n".join(
        f"[{i}] {metadata['path']}:{metadata['start_line']}-"
        f"{metadata['end_line']}\n{text}"
        for i, (text, metadata) in enumerate(zip(documents, metadatas))
    )

    response = completion(
        model=MODEL_ID,
        reasoning_effort="none",
        temperature=0,
        messages=[
            {
                "role": "system",
                "content": (
                    "Rank repository snippets by how directly they answer "
                    "the user's question. Return only candidate numbers, "
                    "comma-separated, from most to least relevant."
                ),
            },
            {
                "role": "user",
                "content": f"Question:\n{query}\n\nCandidates:\n{candidates}",
            },
        ],
    )

    ranking = response.choices[0].message.content or ""
    order = []

    for value in re.findall(r"\d+", ranking):
        index = int(value)
        if index < len(documents) and index not in order:
            order.append(index)

    return order + [i for i in range(len(documents)) if i not in order]


def search_repository(query: str) -> str:
    """Search and rerank relevant repository snippets."""
    results = collection.query(query_texts=[query], n_results=20)
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]

    if not documents:
        return "No matching repository content found."

    order = rerank_candidates(query, documents, metadatas)
    print(f"[reranker] Ranked {len(documents)} candidates; returning top 5.")

    passages = []

    for index in order[:5]:
        text = documents[index]
        metadata = metadatas[index]
        repo_url = metadata["repo_url"].removesuffix(".git").rstrip("/")
        path = metadata["path"]
        start = metadata["start_line"]
        end = metadata["end_line"]
        language = metadata.get("language", "text")
        link = (
            f"{repo_url}/blob/{metadata['commit_sha']}/"
            f"{quote(path)}#L{start}-L{end}"
        )

        passages.append(
            f"Source: [{path}:{start}-{end}]({link})\n"
            f"```{language}\n{text}\n```"
        )

    return "\n\n".join(passages)


root_agent = Agent(
    name="github_repo_agent",
    model=LiteLlm(model=MODEL_ID),
    instruction=(
        "Answer questions using the indexed repository. Always call "
        "search_repository before answering. Use only its results. When "
        "showing code, quote a retrieved snippet exactly; do not rewrite or "
        "invent code. Cite claims with the returned GitHub file-and-line links. "
        "If the repository doesn't contain the answer, say so, Do not create any assumption."
    ),
    tools=[search_repository],
)