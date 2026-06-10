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
TOP_K = 3
POOL_K = 10   # candidate pool kept after RRF so section promotion can rescue lower ranks
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
You are an incident support assistant for the DataPower gateway.
You help support engineers diagnose and resolve active incidents using official platform runbooks.

SCOPE: The available runbooks cover incident troubleshooting only —
connection timeouts, DNS failures, authentication/authorization errors,
message transformation errors, routing errors, security policy violations,
certificate expiry, resource exhaustion, and network infrastructure failures.
They do NOT cover: gateway configuration, service setup, API onboarding,
high availability design, performance tuning, or security protocol setup.

RULES — follow these exactly:
1. Answer ONLY from the RUNBOOK CONTEXT provided. Never use prior knowledge.
2. Before answering, check whether the context actually addresses the query:
   - If the retrieved context contains the information needed to answer, answer
     from it — even if the query mentions configuration objects or settings
     (e.g. allowlist updates, timeout changes during an incident are in scope).
   - If the query is about new-service setup, API onboarding, HA design,
     performance tuning, or security protocol setup AND the context does not
     address it — respond: "No relevant runbook found for this query. The
     available runbooks cover incident troubleshooting patterns only."
   - If the retrieved context does not address the query — respond:
     "No relevant runbook found for this query."
3. Start every answer with ONE direct prose sentence that answers the question
   by mirroring its wording (for "What does X mean?" begin "X means …"; for
   "How do I Y?" begin "To Y, …"). Only after that sentence, use bullet points
   for multi-step or multi-item content.
4. Cite every factual claim with its source: [Pattern N — <Name> | <Section Title>]
5. Do not speculate or add steps not described in the context. Phrase factual
   claims using the runbook's own wording wherever possible — never infer a
   check, cause, or step that is not written in the context.
6. If multiple patterns apply, address each separately with its citation.
7. Answer exactly what was asked, at the level of detail asked (EXCEPTION:
   escalation queries — rule 8 overrides this rule entirely). Never state the
   same fact twice in different formats.
   - Error code queries: 2–4 sentences of prose, no bullets: what the code
     means and its likely cause in the runbook's wording, plus the FIRST
     recommended fix or diagnostic check — but only if a Troubleshooting
     section in the context explicitly covers this error; quote its wording
     and do NOT reproduce the full step list or invent a check.
   - Symptom / likely-cause queries: 2–4 sentences: the likely cause, then
     the first one or two recommended checks from the context — not the full
     procedure.
   - Procedure and immediate-action queries: include EVERY step the runbook
     lists for that task, in order.
   - Triage queries: the decision path only, stated compactly.
8. For escalation queries (any query asking which team or person to escalate
   to, notify, contact, involve, or engage; SLAs; escalation paths): this rule
   OVERRIDES rule 7's brevity — never answer with a single row. After the
   opening sentence that answers the user's specific condition, you MUST
   reproduce the COMPLETE escalation matrix of the matching pattern — one
   bullet per row, INCLUDING rows that do not match the user's condition.
   Use exactly this form:
   - If <condition>, escalate to <team> (SLA: <SLA>).
9. Format responses in markdown: use **bold** for key terms and pattern names,
   and `backticks` for error codes, HTTP status codes, and command names.\
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
    return list(dict.fromkeys(hex_codes + symbolic))


def extract_http_statuses(text: str) -> list[str]:
    # Stored under the separate http_status_codes metadata key at ingest time
    return list(dict.fromkeys(re.findall(r'\bHTTP\s+([2-5][0-9]{2})\b', text, re.IGNORECASE)))


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


_ESCALATION_RE = re.compile(
    r'\b(escalat\w*|who (do i|should i|to) (contact|notify|involve|engage)|'
    r'sla|response time|how long|persists?|persisted|over \d+ min)\b',
    re.IGNORECASE,
)

def promote_error_signatures(chunks: list, query: str) -> list:
    codes    = extract_error_codes(query)
    statuses = extract_http_statuses(query)
    if not (codes or statuses):
        return chunks

    def has_queried_code(c: dict) -> bool:
        meta = c["metadata"]
        return (
            any(code in (meta.get("error_codes") or []) for code in codes)
            or any(s in (meta.get("http_status_codes") or []) for s in statuses)
        )

    sig      = [c for c in chunks if c["metadata"].get("section_type") == "error_signatures"]
    matching = [c for c in sig if has_queried_code(c)]
    # If the queried code pinpoints specific signature chunks, promote only those —
    # promoting every signature table would crowd out the matching pattern's
    # troubleshooting chunks, which carry the recommended fix
    promoted = matching if matching else sig
    promoted_ids = {id(c) for c in promoted}
    rest = [c for c in chunks if id(c) not in promoted_ids]
    return promoted + rest


def promote_escalation_matrix(chunks: list, query: str) -> list:
    if not _ESCALATION_RE.search(query):
        return chunks
    esc   = [c for c in chunks if c["metadata"].get("section_type") == "escalation"]
    other = [c for c in chunks if c["metadata"].get("section_type") != "escalation"]
    return esc + other


_TRIAGE_RE = re.compile(r'\b(root cause|triage|decision tree)\b', re.IGNORECASE)


def is_triage_query(query: str) -> bool:
    # Error-code/status queries keep error-signature priority even if they
    # mention "root cause"
    if extract_error_codes(query) or extract_http_statuses(query):
        return False
    return bool(_TRIAGE_RE.search(query))


def enforce_escalation_matrix(answer: str, chunks: list, query: str) -> str:
    """Guarantee rule 8: escalation answers must contain every matrix row.

    The LLM intermittently answers only the row matching the user's condition;
    append any rows it omitted, verbatim from the retrieved escalation chunk.
    """
    if not _ESCALATION_RE.search(query) or "no relevant runbook" in answer.lower():
        return answer
    esc = next(
        (c for c in chunks if c["metadata"].get("section_type") == "escalation"),
        None,
    )
    if esc is None:
        return answer
    meta = esc["metadata"]
    cite = f"[{meta['pattern_id']} — {meta['pattern_name']} | {meta['section_title']}]"
    answer_lower = answer.lower()
    missing = []
    for line in esc["content"].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        condition, team, sla = cells
        if condition.lower() in ("condition", "") or set(condition) <= {"-", " ", ":"}:
            continue  # header / separator rows
        if condition.lower() in answer_lower:
            continue
        missing.append(f"- If **{condition}**, escalate to **{team}** (SLA: {sla}). {cite}")
    if missing:
        answer = answer.rstrip() + "\n" + "\n".join(missing)
    return answer


def promote_triage(chunks: list, query: str) -> list:
    if not is_triage_query(query):
        return chunks
    tri   = [c for c in chunks if c["metadata"].get("section_type") == "triage"]
    other = [c for c in chunks if c["metadata"].get("section_type") != "triage"]
    return tri + other


def filter_to_dominant_pattern(chunks: list, query: str) -> list:
    if not _ESCALATION_RE.search(query) or not chunks:
        return chunks
    # Judge the dominant pattern on the best-ranked (unpromoted) chunks only,
    # so escalation matrices of unrelated patterns deeper in the pool can't
    # hijack the pattern choice
    pattern_counts: dict[str, int] = {}
    for c in chunks[:TOP_K]:
        pid = c["metadata"]["pattern_id"]
        pattern_counts[pid] = pattern_counts.get(pid, 0) + 1
    dominant_pattern = max(pattern_counts, key=pattern_counts.get)
    return [c for c in chunks if c["metadata"]["pattern_id"] == dominant_pattern]


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
    for doc_id in ranked_ids[:POOL_K]:
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

    # 5. Error code metadata-filtered retrieval (hex + symbolic + HTTP status)
    hex_results = []
    code_filters = (
        [{"error_codes": [code]} for code in extract_error_codes(retrieval_query)]
        + [{"http_status_codes": [status]} for status in extract_http_statuses(retrieval_query)]
    )
    for code_filter in code_filters:
        try:
            rows = _get_supabase().rpc("match_documents", {
                "query_embedding": embedding,
                "match_count": RETRIEVAL_K,
                "filter": code_filter,
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

    # 5a. Triage-section retrieval — the decision-tree chunks are mostly ASCII
    # diagrams that rank poorly in dense/sparse search, so fetch them directly
    triage_results = []
    if is_triage_query(retrieval_query):
        try:
            triage_results = _get_supabase().rpc("match_documents", {
                "query_embedding": embedding,
                "match_count": RETRIEVAL_K,
                "filter": {"section_type": "triage"},
            }).execute().data or []
        except Exception:
            triage_results = []

    # 6. RRF merge — keeps a POOL_K candidate pool so promotion can act below rank 3
    chunks = reciprocal_rank_fusion(dense_results, sparse_results, deduped_hex, triage_results)

    # 6a. Deduplicate — max 3 chunks per pattern to avoid one pattern monopolising slots
    chunks = deduplicate_by_pattern(chunks)

    # 6b. Promote error signature chunks to top positions for error code queries
    chunks = promote_error_signatures(chunks, retrieval_query)

    # 6c. For escalation queries, lock onto the dominant pattern of the
    #     unpromoted ranking, then promote its escalation matrix chunk
    chunks = filter_to_dominant_pattern(chunks, retrieval_query)
    chunks = promote_escalation_matrix(chunks, retrieval_query)

    # 6d. Promote triage decision-tree chunks for root-cause/triage queries
    chunks = promote_triage(chunks, retrieval_query)

    # 6e. Truncate the pool to the final context size
    chunks = chunks[:TOP_K]

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

    # Deterministic guarantee of rule 8 (complete escalation matrix)
    answer = enforce_escalation_matrix(answer, chunks, retrieval_query)

    sources = [
        Source(
            pattern_id=c["metadata"]["pattern_id"],
            section_title=c["metadata"]["section_title"],
            score=c["rrf_score"],
        )
        for c in chunks
    ]

    return AnalyzeResponse(answer=answer, sources=sources)
