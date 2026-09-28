---
title: "HANDOVER — Build the Second Brain"
date: "2026-07-05"
audience: "Claude Code / Hermes build agent"
inputs: "This repo (~/brain-engine, v1.1 — see CHANGELOG.md) + second-brain-ideal-setup-spec.md + original build brief"
---

> **Historical planning doc.** Written 2026-07-05 to kick off the build. The
> phases and acceptance criteria below reflect the plan as it stood then, not
> the repo as it stands now — the tree, script list, and rule count below are
> out of date (the repo has grown to 8 scripts and 21 vault + 2 engine lint
> rules since). For current state, read `README.md` first, then `BRAIN.md`.
> Kept here for build-history context, not as a live spec.

# HANDOVER — Build the Second Brain

You are implementing a compounding second brain on the existing Hermes stack.
This repo IS the engine — most of the design work is done. Your job is wiring,
migration, and verification. Read `BRAIN.md` first; it is the law for every
agent that will touch the vault, including you during this build.

## What's in this repo (as of 2026-07-05 — see README.md for current state)

```
brain-engine/
├── BRAIN.md                     # schema v1 — load per Hermes profile (SOUL.md hook)
├── HANDOVER.md                  # this file
├── skills/
│   ├── retriever/SKILL.md       # read-only lookup — every skill's pre-production
│   ├── updater/SKILL.md         # SOLE write path — drains deposit queue
│   ├── dreamer/SKILL.md         # weekly consolidation cron
│   └── reviewer/SKILL.md        # transcript mining / backfill
├── lint/rules.md                # 12 vault rules + 2 engine rules, severities, outputs (now 21 + 2)
├── scripts/
│   ├── rebuild_index.py         # WORKING — derived index generator (+ --check for lint rule 4)
│   └── capture_to_raw.py        # WORKING — append-only raw capture helper
└── templates/
    ├── page-{entity,concept,decision,source,moc,project}.md
    └── vault-init/              # empty vault scaffold incl. _system seeds
```

Design authority chain: BRAIN.md > ideal-setup spec > original build brief.
The spec's §4 (write governance) and §8 (productization boundary) explain the
*why* behind anything that looks unusual here.

## Build phases

### Phase 1 — Vault init + migration (half day)
1. Initialize `~/wiki` as a git repo if not already; commit current state as
   `init: pre-migration snapshot` (this is the rollback point). Configure a
   private remote (or scheduled offsite sync) and push — backup is mandatory
   per BRAIN.md §8 before any autonomous writing begins.
2. Overlay `templates/vault-init/` structure onto the existing wiki WITHOUT
   destroying existing content: create missing dirs (`raw/`, `corpus/`,
   `overlays/*`, `_system/pending-deposits/`, `_system/lint-reports/`),
   seed `taxonomy.md` from tags currently in use (dedupe, define each).
3. Migrate existing pages to schema v1 frontmatter: add missing fields
   (`confidence: medium` default, `valid_from` = `created`, `superseded_by:
   null`, `overlay:` by best judgment). Existing `learnings/` pages map to
   `concepts/` with `overlay: dev` unless clearly otherwise.
4. Reconcile `_system/INDEX.md` (human-oriented) with the new derived
   `index.md`: keep the old file as `references/human-index.md` if it has
   curation value; `index.md` is script-owned from now on.
5. Run `scripts/rebuild_index.py`. Commit as `deposit(migration): schema v1`.

**Acceptance:** every page parses under `rebuild_index.py` with zero WARNs;
`--check` passes; git history shows the snapshot + migration commits.

### Phase 2 — Startup wiring (1–2 h)
1. Wire BRAIN.md into every Hermes profile load (SOUL.md hook per the original
   brief's Layer 0), and index.md as the mandatory first read (retriever
   skill step 1).
2. Add to CLAUDE.md for Hermes dev work: "Always check ~/wiki (index.md first)
   before answering questions about architecture, patterns, or decisions."
3. Optional cron: `rebuild_index.py` every 6h as belt-and-braces.

**Acceptance:** a fresh session, asked anything vault-covered, demonstrably
reads index.md before answering (verify in transcript).

### Phase 3 — Updater + deposit queue (half day) — BEFORE any writing loop
1. Install the four role skills into the Hermes skill library.
2. Implement the updater invocation: session-end hook and/or a frequent cron
   that runs the updater skill when `pending-deposits/` is non-empty.
   Serial execution only — one updater instance at a time (lockfile).
3. Test with hand-written intent files covering: create_concept,
   update_entity, a deliberate contradiction, a rogue tag (must be rejected
   to `rejected/` with reason), log_only, a malformed YAML file (must be
   rejected, not guessed at), a create targeting an existing path (must
   convert to update), and an intent citing a source that contains
   agent-addressed instructions (must be flagged `[INJECTION-SUSPECT]`,
   capped at medium confidence, and the instruction ignored).
4. Test lock contention: start a second updater while one holds
   `_system/.write-lock` — it must exit cleanly, not interleave.

**Acceptance:** all eight test intents behave per updater SKILL.md; one git
commit per intent file with `Session:` trailer; log entries present; index
rebuilt; rejections carry reason lines; lock contention test passes.

### Phase 4 — Writing loop pilot (half day)
1. Pick one skill (creative-agency or personal-assistant-bot). Replace any
   direct wiki writes with intent emission per BRAIN.md §6. Pre-production
   step = retriever skill.
2. Run end-to-end on a real task; verify deposit lands via updater.
3. Document the retrofit as a diff template for the remaining skills.

**Acceptance:** original brief's P1 criteria + zero direct writes from the
skill (verify: no vault commits authored outside the updater path).

### Phase 5 — Raw layer + capture pipelines (2–3 h, parallel-safe)
1. Wire `capture_to_raw.py` into PA intake, web_extract, and competitor
   monitors BEFORE their processing steps.
2. Lint rule 3 depends on raw/ being committed — use `--commit` or batch.

**Acceptance:** original brief's P1 raw criteria; a test web_extract lands a
timestamped raw file with source URL before any summary exists.

### Phase 6 — Reviewer backfill (half day, highest early leverage)
1. Run reviewer in backfill mode over historical Hermes sessions, ≤10 per
   run. Start with the most recent month, work backwards.
2. Let the updater drain between batches (lint rule 12: no intent >24h old).
3. Verify `_system/reviewer-ledger.yaml` grows with each batch and re-runs
   skip already-mined sessions.

**Acceptance:** vault page count grows materially from history alone;
contradictions between old transcripts and current pages surface as
`contradicts:` intents rather than silent overwrites.

### Phase 7 — Rollout + corpus + dreamer + lint (ongoing)
1. Retrofit remaining output-producing skills using the Phase 4 template
   (target ≥5 skills within 2 weeks).
2. Corpus per original brief P2 (`corpus/linkedin-posts.md`, writing samples);
   corpus updates flow as deposit intents when content publishes.
3. Dreamer weekly cron; first run seeds initial MOCs (brief's P3).
4. Lint weekly cron implementing `lint/rules.md`; rules 1–7 + 12 first,
   8–11 second pass. Engine rules E1–E2 run on this repo.

**Acceptance:** success metrics table below is being measured.

## Success metrics (report weekly in lint output)

| Metric | Target |
|--------|--------|
| Sessions reading index.md first | 100% (measured: weekly lint samples 5 recent transcripts and checks for an index.md read before first substantive output) |
| Skills emitting deposit intents | ≥5 within 2 weeks |
| Intents applied within 24h | 100% |
| Provenance marks on synthesis-heavy pages (lint sample) | ≥90% |
| Index drift at lint | 0 |
| Orphans / stale pages | <5% / <10% |
| Raw files/week, wiki pages updated/week | ≥10 / ≥20 |

## Backlog (do NOT build now — recorded so they aren't forgotten)

- Onboarding-ingest skill for vault-init (brain-dump conversation → reviewer
  mines it → first pages). Required for Hermes Instant template, not for
  the author's own vault.
- qmd-style hybrid BM25/vector index over the vault if Honcho proves
  mismatched for vault-wide semantic retrieval (spec §5).
- git-lfs decision if binary captures become frequent (BRAIN.md §8).
- OKF field-name mapping pass once the spec stabilizes (spec §8).

## Hard rules for you, the build agent

1. You are bound by BRAIN.md during this build. Your own knowledge deposits
   about this build (decisions, gotchas) go through intents like everyone
   else's — the build should be the first entry in the brain's own history.
2. Do not put personal data, names, or vault content into this engine repo
   (lint E1). Engine and vault stay separable — the engine is a future product.
3. Anything ambiguous: prefer the conservative reading, record the question
   as a decision page draft, and ask rather than improvise on schema.
4. Model routing note: updater and reviewer are judgment-heavy (Sonnet-tier
   per the existing routing strategy); retriever reads are cheap (Haiku-tier);
   dreamer and lint are batch-friendly (Batch API + prompt caching).
