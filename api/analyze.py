import os
import re

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel
from supabase import create_client

load_dotenv()

router = APIRouter()

_supabase = None
_openai = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not url or not key:
            raise HTTPException(status_code=500, detail="SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")
        _supabase = create_client(url, key)
    return _supabase


def _get_openai():
    global _openai
    if _openai is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY not configured")
        _openai = OpenAI(api_key=key)
    return _openai

EMBED_MODEL = "text-embedding-3-small"
CHAT_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4-mini")
TOP_K = 5
RETRIEVAL_K = 10
RRF_K = 60
HISTORY_WINDOW = int(os.environ.get("HISTORY_WINDOW", "10"))  # max conversation turns kept

_FTS_STOP_WORDS = {
    "are", "the", "and", "for", "not", "how", "what", "does", "when",
    "why", "where", "who", "which", "this", "that", "from", "with",
    "all", "have", "has", "had", "was", "were", "will", "been", "being",
    "can", "you", "your", "its", "our", "their",
}

SYSTEM_PROMPT = """\
You are an incident support assistant for the ESGA DataPower gateway.
You help support engineers diagnose and resolve active incidents using official ESGA runbooks.

SCOPE: The available runbooks cover incident troubleshooting only —
connection timeouts, DNS failures, authentication/authorization errors,
message transformation errors, routing errors, security policy violations,
certificate expiry, resource exhaustion, and network infrastructure failures.
They do NOT cover: gateway configuration, service setup, API onboarding,
high availability design, performance tuning, or security protocol setup.

RULES — follow these exactly:
1. Answer ONLY from the RUNBOOK CONTEXT provided. Never use prior knowledge.
2. Before answering, check whether the context actually addresses the query:
   - If the query is about configuration, setup, or anything outside the scope
     above — respond: "No relevant runbook found for this query. The available
     runbooks cover incident troubleshooting patterns only."
   - If the retrieved context does not address the query — respond:
     "No relevant runbook found for this query."
3. Cite every factual claim with its source: [Pattern N — <Name> | <Section Title>]
4. Do not speculate or add steps not described in the context.
5. If multiple patterns apply, address each separately with its citation.
6. Be concise. Answer only what was asked — do not volunteer unrequested steps or
   background. For error code queries: 1–2 sentences identifying the code and its cause.
   For triage queries: state the decision path only. For procedure queries: list only
   the steps directly relevant to the question asked.\
"""

_REFORMULATE_PROMPT = """\
You are rewriting a user query to make it self-contained for a search engine.
Given the conversation history below, rewrite the CURRENT QUERY so it resolves
all pronouns and references without requiring the history to be understood.
Output only the rewritten query — no preamble, no punctuation changes beyond
what is needed, no explanation.

CONVERSATION HISTORY:
{history}

CURRENT QUERY: {query}

REWRITTEN QUERY:\
"""


# ── Pydantic models ───────────────────────────────────────────────────────────

class HistoryMessage(BaseModel):
    role: str    # "user" or "assistant"
    content: str


class AnalyzeRequest(BaseModel):
    query: str
    history: list[HistoryMessage] = []


class Source(BaseModel):
    pattern_id: str
    section_title: str
    score: float


class AnalyzeResponse(BaseModel):
    answer: str
    sources: list[Source]


# ── Retrieval helpers ─────────────────────────────────────────────────────────

def extract_error_codes(text: str) -> list[str]:
    hex_codes   = re.findall(r'0x[0-9A-Fa-f]+', text, re.IGNORECASE)
    symbolic    = re.findall(r'\b([A-Z][A-Z_0-9]{4,})\b', text)
    http_status = re.findall(r'\bHTTP\s+([2-5][0-9]{2})\b', text, re.IGNORECASE)
    return list(dict.fromkeys(hex_codes + symbolic + http_status))


def build_fts_query(text: str) -> str:
    tokens = re.findall(r'[a-zA-Z]{3,}', text)
    unique = list(dict.fromkeys(
        t.lower() for t in tokens if t.lower() not in _FTS_STOP_WORDS
    ))
    return " | ".join(unique) if unique else ""


def deduplicate_by_pattern(chunks: list, max_per_pattern: int = 3) -> list:
    seen: dict = {}
    out: list  = []
    for c in chunks:
        pid = c["metadata"]["pattern_id"]
        if seen.get(pid, 0) < max_per_pattern:
            out.append(c)
            seen[pid] = seen.get(pid, 0) + 1
    return out


def promote_error_signatures(chunks: list, query: str) -> list:
    if not extract_error_codes(query):
        return chunks
    sig   = [c for c in chunks if c["metadata"].get("section_type") == "error_signatures"]
    other = [c for c in chunks if c["metadata"].get("section_type") != "error_signatures"]
    return (sig + other)[:TOP_K]


def reciprocal_rank_fusion(*result_lists: list) -> list:
    scores: dict = {}
    chunk_data: dict = {}
    for results in result_lists:
        for rank, doc in enumerate(results):
            doc_id = doc["id"]
            scores[doc_id] = scores.get(doc_id, 0) + 1 / (RRF_K + rank + 1)
            if doc_id not in chunk_data:
                chunk_data[doc_id] = doc
    ranked_ids = sorted(scores, key=lambda x: scores[x], reverse=True)
    output = []
    for doc_id in ranked_ids[:TOP_K]:
        chunk = dict(chunk_data[doc_id])
        chunk["rrf_score"] = round(scores[doc_id], 6)
        output.append(chunk)
    return output


# ── Conversation helpers ──────────────────────────────────────────────────────

def trim_history(history: list[HistoryMessage]) -> list[HistoryMessage]:
    """Keep the most recent HISTORY_WINDOW turns (1 turn = user + assistant pair)."""
    max_messages = HISTORY_WINDOW * 2
    return history[-max_messages:] if len(history) > max_messages else history


def reformulate_query(query: str, history: list[HistoryMessage]) -> str:
    """
    Rewrite the query to be self-contained using the last 3 turns of history.
    Returns the original query unchanged if history is empty or reformulation fails.
    """
    if not history:
        return query

    recent = history[-6:]  # last 3 turns (6 messages)
    history_text = "\n".join(f"{m.role.upper()}: {m.content}" for m in recent)

    try:
        completion = _get_openai().chat.completions.create(
            model=CHAT_MODEL,
            temperature=0,
            max_completion_tokens=128,
            messages=[{
                "role": "user",
                "content": _REFORMULATE_PROMPT.format(
                    history=history_text,
                    query=query,
                ),
            }],
        )
        rewritten = completion.choices[0].message.content.strip()
        return rewritten if rewritten else query
    except Exception:
        return query  # degrade gracefully — retrieval still works with original


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    history = trim_history(req.history)

    # 1. Reformulate query using conversation history (for retrieval only)
    retrieval_query = reformulate_query(req.query, history)

    # 2. Embed the reformulated query
    try:
        embedding = _get_openai().embeddings.create(
            model=EMBED_MODEL,
            input=retrieval_query,
        ).data[0].embedding
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Embedding failed: {e}")

    # 3. Dense retrieval
    try:
        dense_results = _get_supabase().rpc("match_documents", {
            "query_embedding": embedding,
            "match_count": RETRIEVAL_K,
            "filter": {},
        }).execute().data or []
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Dense retrieval failed: {e}")

    # 4. Sparse retrieval (FTS)
    sparse_results = []
    fts_query = build_fts_query(retrieval_query)
    if fts_query:
        try:
            sparse_results = _get_supabase().rpc("match_documents_fts", {
                "query_text": fts_query,
                "match_count": RETRIEVAL_K,
                "filter": {},
            }).execute().data or []
        except Exception:
            sparse_results = []

    # 5. Error code metadata-filtered retrieval (hex + symbolic)
    hex_results = []
    for code in extract_error_codes(retrieval_query):
        try:
            rows = _get_supabase().rpc("match_documents", {
                "query_embedding": embedding,
                "match_count": RETRIEVAL_K,
                "filter": {"error_codes": [code]},
            }).execute().data or []
            hex_results.extend(rows)
        except Exception:
            pass
    seen: set = set()
    deduped_hex: list = []
    for row in hex_results:
        if row["id"] not in seen:
            seen.add(row["id"])
            deduped_hex.append(row)

    # 6. RRF merge
    chunks = reciprocal_rank_fusion(dense_results, sparse_results, deduped_hex)

    # 6a. Deduplicate — max 2 chunks per pattern to avoid one pattern monopolising slots
    chunks = deduplicate_by_pattern(chunks)

    # 6b. Promote error signature chunks to top positions for error code queries
    chunks = promote_error_signatures(chunks, retrieval_query)

    # 7. Format context
    context_parts = []
    for chunk in chunks:
        meta = chunk["metadata"]
        header = f"[{meta['pattern_id']} — {meta['pattern_name']} | {meta['section_title']}]"
        context_parts.append(f"{header}\n{chunk['content']}")
    context = "\n\n---\n\n".join(context_parts)

    # 8. Build messages — history turns first, then current query with context
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({
        "role": "user",
        "content": f"RUNBOOK CONTEXT:\n{context}\n\nQuery: {req.query}",
    })

    # 9. Generate answer
    try:
        completion = _get_openai().chat.completions.create(
            model=CHAT_MODEL,
            temperature=0,
            max_completion_tokens=512,
            messages=messages,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e}")

    answer = completion.choices[0].message.content

    sources = [
        Source(
            pattern_id=c["metadata"]["pattern_id"],
            section_title=c["metadata"]["section_title"],
            score=c["rrf_score"],
        )
        for c in chunks
    ]

    return AnalyzeResponse(answer=answer, sources=sources)
