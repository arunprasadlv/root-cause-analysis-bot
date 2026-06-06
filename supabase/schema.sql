-- Run once in the Supabase SQL editor before first ingestion.

-- Enable pgvector extension
create extension if not exists vector;

-- Main documents table
create table documents (
  id        bigserial primary key,
  content   text        not null,          -- clean chunk body (no header)
  metadata  jsonb       not null default '{}',
  embedding vector(1536)                   -- text-embedding-3-small dimensions
);

-- Full-text search column (auto-maintained by Postgres)
alter table documents
  add column fts tsvector
  generated always as (to_tsvector('english', content)) stored;

-- Indexes
create index on documents using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);
create index on documents using gin(fts);
create index on documents using gin(metadata);

-- ─── Phase 2: match_documents RPC (dense vector search) ──────────────────────

create or replace function match_documents (
  query_embedding  vector(1536),
  match_count      int     default 10,
  filter           jsonb   default '{}'
)
returns table (
  id         bigint,
  content    text,
  metadata   jsonb,
  similarity float
)
language plpgsql
as $$
begin
  return query
  select
    d.id,
    d.content,
    d.metadata,
    1 - (d.embedding <=> query_embedding) as similarity
  from documents d
  where d.metadata @> filter
  order by d.embedding <=> query_embedding
  limit match_count;
end;
$$;

-- ─── Phase 3: match_documents_fts RPC (sparse FTS search) ────────────────────

create or replace function match_documents_fts (
  query_text   text,
  match_count  int   default 10,
  filter       jsonb default '{}'
)
returns table (
  id       bigint,
  content  text,
  metadata jsonb,
  rank     float
)
language plpgsql
as $$
begin
  return query
  select
    d.id,
    d.content,
    d.metadata,
    ts_rank(d.fts, to_tsquery('english', query_text))::float as rank
  from documents d
  where d.fts @@ to_tsquery('english', query_text)
    and d.metadata @> filter
  order by rank desc
  limit match_count;
end;
$$;

-- ─── Phase 7: eval_runs table (evaluation results storage) ───────────────────

create table eval_runs (
  id         bigserial primary key,
  phase      text        not null,   -- e.g. 'phase2_baseline', 'phase3_hybrid'
  run_at     timestamptz default now(),
  retrieval  jsonb,                  -- HR@5, MRR@5, precision, recall
  generation jsonb,                  -- RAGAS metrics
  system     jsonb,                  -- citation_accuracy, negative_handling_rate
  notes      text
);
