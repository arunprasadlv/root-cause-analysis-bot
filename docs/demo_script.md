# Demo Video Script — ESGA DataPower RCA Assistant

**Format:** silent screen capture + on-screen captions (no voiceover)
**Duration:** ~5:00 · **Resolution:** 1920×1080, 30fps · **Browser zoom:** 110–125%

Captions carry all the explanation, so keep each overlay short (one line, ≤ ~10
words) and on screen long enough to read (~3s minimum). Title cards introduce
each feature; callout captions point at what's happening.

---

## Pre-production checklist

- [ ] `.env` has `OPENAI_API_KEY` + `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` (live answers need these).
- [ ] Start server: `python -m uvicorn api.index:app --host 0.0.0.0 --port 8000`
- [ ] **Warm up**: send one throwaway query and wait for a reply *before* recording (first call is slow, ~30–60s cold start).
- [ ] Pre-run every scene query once so retrieval is cached and you've seen each answer.
- [ ] Browser: 1080p window, zoom ~115%, bookmarks bar + extensions hidden, clean profile.
- [ ] Clear chat history between scenes — **except Scene 8** (multi-turn needs the prior turn on screen).

---

## Storyboard (≈300s)

Legend: **TITLE** = full title card · **CAP** = caption overlay during action ·
**DO** = on-screen action (click/type).

### Scene 1 — Hook · 0:00–0:25
- **DO:** Landing page (`http://localhost:8000/`), slowly scroll the hero.
- **TITLE:** "ESGA DataPower RCA Assistant"
- **CAP:** "Mid-incident, support engineers hunt through dense runbooks."
- **CAP:** "What if you could just ask?"

### Scene 2 — Architecture · 0:25–0:55
- **DO:** Scroll to the Architecture section.
- **TITLE:** "How it works"
- **CAP:** "Runbooks → embeddings in Supabase (pgvector + full-text search)"
- **CAP:** "Hybrid retrieval + OpenAI = grounded, cited answers"

### Scene 3 — Error-code lookup + citations · 0:55–1:35
- **DO:** Open `/chat`. Click chip 🔴 **"What does error code 0x00d30003 mean on DataPower?"**
- **TITLE:** "Feature 1 · Error-code lookup"
- **CAP:** "Paste any DataPower error code"
- **CAP (point at citation):** "Every claim cites its source — [Pattern · Section]"
- **CAP:** "Grounded in the runbook, not guessed"

### Scene 4 — Symptom → likely cause · 1:35–2:05
- **DO:** Click chip 🌐 **"Multiple backend services are timing out simultaneously. What should I check first?"**
- **TITLE:** "Feature 2 · Symptom triage"
- **CAP:** "Describe what you're seeing"
- **CAP:** "It reasons to the likely root cause — a network/infra issue, not one backend"

### Scene 5 — Decision-tree guidance · 2:05–2:40
- **DO:** Type **"A backend connection timeout has occurred. How do I determine the root cause?"**
- **TITLE:** "Feature 3 · Guided triage"
- **CAP:** "Returns the runbook's decision tree"
- **CAP:** "Surfaces the exact path plain keyword search misses"

### Scene 6 — Step-by-step procedure · 2:40–3:10
- **DO:** Click chip 🔑 **"All users are getting HTTP 401 after a JWT signing key rotation. How do I fix this?"**
- **TITLE:** "Feature 4 · Step-by-step fixes"
- **CAP:** "Ordered, runbook-exact remediation steps"

### Scene 7 — Escalation matrix · 3:10–3:45
- **DO:** Click chip 📋 **"A backend timeout issue has persisted for over 30 minutes. Who do I escalate to and what is the SLA?"**
- **TITLE:** "Feature 5 · Escalation paths"
- **CAP:** "Returns the complete escalation matrix"
- **CAP:** "Every team, every SLA — nothing dropped"

### Scene 8 — Multi-turn conversation · 3:45–4:15
- **DO:** Re-run chip 🔴 (0x00d30003), wait for the answer, then type the follow-up **"How do I fix it?"**
- **TITLE:** "Feature 6 · Multi-turn context"
- **CAP (point at follow-up):** "No need to restate the error"
- **CAP:** "\"it\" is resolved from the conversation automatically"

### Scene 9 — Guardrails · 4:15–4:40
- **DO:** Type **"How do I configure a new Multi-Protocol Gateway service from scratch?"** → expect "No relevant runbook found…".
- **TITLE:** "Feature 7 · Honest guardrails"
- **CAP:** "Out of scope? It says so."
- **CAP:** "No hallucinated steps — answers stay inside the runbooks"

### Scene 10 — Eval report + close · 4:40–5:00
- **DO:** Open `/eval-report` (top-right link). Scroll the metrics.
- **TITLE:** "Measured quality"
- **CAP:** "Hit Rate@5 1.00 · Faithfulness 0.97 · Citations 100% · Hallucination 3%"
- **CAP / end card:** "ESGA DataPower RCA Assistant — ask your runbooks."

---

## Run sheet (exact ordered actions)

1. `/` — scroll hero, then Architecture section.
2. `/chat` — click chip: *What does error code 0x00d30003 mean on DataPower?*
3. (new chat) chip: *Multiple backend services are timing out simultaneously. What should I check first?*
4. (new chat) type: *A backend connection timeout has occurred. How do I determine the root cause?*
5. (new chat) chip: *All users are getting HTTP 401 after a JWT signing key rotation. How do I fix this?*
6. (new chat) chip: *A backend timeout issue has persisted for over 30 minutes. Who do I escalate to and what is the SLA?*
7. (new chat) chip: *What does error code 0x00d30003 mean on DataPower?* → wait → type: *How do I fix it?*
8. (new chat) type: *How do I configure a new Multi-Protocol Gateway service from scratch?*
9. `/eval-report` — scroll metrics, hold on the summary.

> "new chat" = refresh the chat page (or clear history) so each scene starts clean. **Do NOT refresh between the two turns in step 7.**

---

## Production settings
- OBS Studio (or Windows 11 Clipchamp / `Win+G`). 1920×1080, 30fps.
- Record clean silent passes per scene; assemble + caption in post.
- Move the cursor deliberately; pause on citations/sources so the eye can follow.

## Post-production
- **Cut LLM latency** — speed answer-streaming gaps 4× or hard-cut them; this is the #1 thing that keeps it under 5:00.
- Add the title card per scene (from **TITLE** above) and timed caption overlays (from **CAP**).
- Keep captions on screen ≥ 3s; high-contrast, lower-third or near the element they point at.
- Optional: subtle background music (low), since there's no voiceover.
- Export 1080p MP4 (H.264).
