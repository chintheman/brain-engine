#!/usr/bin/env python3
"""rebuild_index.py — regenerate ~/wiki/_system/index.md from page frontmatter.

The index is a DERIVED artifact. This script is the only thing that writes it.
Run by the updater after every deposit batch, by the dreamer after runs, and
optionally by a 6-hourly cron.

Usage: rebuild_index.py [--vault ~/wiki] [--check]
  --check: regenerate to stdout diff only (lint rule 4); exit 1 on drift.

Stdlib only. Parses minimal YAML frontmatter (flat keys, inline lists) to
avoid a dependency; pages are schema-controlled so this is safe.
"""
import argparse
import datetime as dt
import difflib
import os
import re
import sys

SECTIONS = [
    ("entity", "Entities"),
    ("concept", "Concepts"),
    ("moc", "MOCs"),
    ("project", "Projects (Active)"),
    ("decision", "Decisions"),
    ("source", "Sources"),
    ("digest", "Digests"),
]
SCAN_DIRS = ["entities", "concepts", "mocs", "projects", "decisions",
             "sources",
             "overlays/content", "overlays/dev", "overlays/personal"]
RECENT_DAYS = 7

# Workspace folders: live working files (handovers, drafts, exports) that are
# navigated by their own README/STATE, change daily, and are not catalogue pages.
# Files there WITHOUT frontmatter are skipped quietly instead of failing the gate;
# files WITH frontmatter are still catalogued as normal (85 were, on 2026-10-07).
# Wiki search (~/.hermes/scripts/wiki-fts5) ingests all of them either way. Added 2026-10-07: 70+
# ai-studio working files had no frontmatter and kept the 6-hourly gate red.
WORKSPACE_DIRS = {"projects/ai-studio"}

# Digests are a cron-produced daily stream. Rendering all of them would roughly
# double this file, which BRAIN.md §0.1 makes a mandatory read at every session
# start and §9 calls the progressive-disclosure gate. Only the recent window is
# listed by name; the rest are counted, so the class is visible without the
# Tier-0 artifact paying for every page.
DIGEST_WINDOW_DAYS = 14

# Folder is the category. Each directory declares the SET of types its pages may
# carry; frontmatter must name a type from its folder's set. A single-type set is
# the normal case — `concepts/` is the deliberate exception, holding the daily
# digest stream alongside durable concepts (BRAIN.md v1.3).
ALL_TYPES = {t for t, _ in SECTIONS}
FOLDER_TYPES = {
    "entities": {"entity"},
    "concepts": {"concept", "digest"},
    "mocs": {"moc"},
    "projects": {"project"},
    "decisions": {"decision"},
    "sources": {"source"},
    # Overlays carry ordinary page types tagged by the `overlay:` field (§2).
    "overlays/content": ALL_TYPES,
    "overlays/dev": ALL_TYPES,
    "overlays/personal": ALL_TYPES,
}

FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)



# YAML spells null six ways and an empty list two more. Treating any of them as
# "this page is superseded" both exempts it from --check-types and relocates a live
# page into the Superseded section — the exact failure the gate exists to prevent,
# reachable by one character. A page is superseded only when the field names a target.
_NULLISH = {"", "null", "~", "none", "nil", "false", "0", "[]", "[ ]", "{}"}


def _target_exists(vault, value, self_rel=None):
    """True when `superseded_by` names a DIFFERENT page that is on disk.

    Self-reference is the escape hatch: a page with the wrong type for its folder
    could point at itself and be exempt forever. A redirect to yourself is not a
    redirect.
    """
    raw = str(value).split("#", 1)[0].strip().strip('"').strip("'")
    name = raw.strip("[]").split("|", 1)[0].strip()
    if not name:
        return False
    if self_rel:
        # Same PATH only. A same-basename target in a different directory is the
        # canonical retirement (entities/x superseded by projects/x), not a
        # self-reference — comparing basenames gated exactly that case.
        me = os.path.splitext(self_rel)[0].lower().lstrip("./")
        them = os.path.splitext(name)[0].lower().lstrip("./")
        if them == me:
            return False
    if name.endswith(".md") and os.path.exists(os.path.join(vault, name)):
        return True
    # Case-insensitively, because the vault lives on a case-insensitive filesystem
    # and a link written [[Projects/Agent-Adoption]] names the same real file as
    # [[projects/agent-adoption]]. Matching case-sensitively gated a page whose
    # redirect target genuinely exists.
    base = os.path.basename(name).lower()
    for d in SCAN_DIRS:
        root = os.path.join(vault, d)
        for sub, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith((".", "_"))]
            lowered = {f.lower() for f in files}
            if f"{base}.md" in lowered or base in lowered:
                return True
    return False


def _is_superseded(value):
    v = str(value).split("#", 1)[0].strip().strip('"').strip("'").lower()
    return v not in _NULLISH


def parse_frontmatter(text, path="?"):
    """Minimal YAML: flat keys + inline lists (schema rule). Block/dash lists
    are tolerated with a warning so a non-conforming page degrades loudly,
    not silently."""
    m = FM_RE.match(text)
    if not m:
        return None
    fm, cur = {}, None
    for line in m.group(1).splitlines():
        if line.strip().startswith("#") or not line.strip():
            continue
        if line.startswith((" ", "\t")) or line.lstrip().startswith("- "):
            if cur is not None:
                item = line.strip().lstrip("- ").strip().strip('"')
                fm[cur] = (fm[cur] + ", " + item).lstrip(", ") if fm[cur] else item
                print(f"WARN: block list in frontmatter (use inline lists): {path} :: {cur}",
                      file=sys.stderr)
            continue
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        cur = key.strip()
        fm[cur] = val.strip().strip('"').strip("[]")
    return fm


def summary_line(text):
    m = re.search(r"^## Summary\s*\n+(.+?)(?:\n\n|\n##|\Z)", text,
                  re.DOTALL | re.MULTILINE)
    if not m:
        return ""
    s = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.DOTALL)
    s = " ".join(s.split())
    return (s[:157] + "...") if len(s) > 160 else s


def collect(vault, skipped=None):
    """Collect every page under SCAN_DIRS.

    `skipped`, if given, accumulates paths that were dropped for unparseable or
    absent frontmatter. Those pages are invisible to the index, which BRAIN.md §9
    makes the mandatory Tier-0 read, so a silent drop is the same defect class as a
    wrong type — it just fails by omission instead of by contradiction. The WARN
    alone is not enough: it goes to stderr, and under cron nobody reads stderr.

    The walk is recursive. It was depth-1 until 2026-09-11, which made every page in
    a nested folder invisible with no warning at all.
    """
    pages = []
    for d in SCAN_DIRS:
        root = os.path.join(vault, d)
        if not os.path.isdir(root):
            continue
        found = []
        for sub, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith((".", "_"))]
            found.extend(os.path.join(sub, f) for f in files if f.endswith(".md"))
        for path in sorted(found):
            fn = os.path.basename(path)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except (UnicodeDecodeError, OSError) as exc:
                print(f"WARN: unreadable ({exc.__class__.__name__}): {path}", file=sys.stderr)
                if skipped is not None:
                    skipped.append(os.path.relpath(path, vault))
                continue
            fm = parse_frontmatter(text, path)
            if not fm or "type" not in fm:
                rel = os.path.relpath(path, vault)
                if any(rel.startswith(w + os.sep) for w in WORKSPACE_DIRS):
                    continue      # workspace working file: not a catalogue page, by design
                print(f"WARN: no/invalid frontmatter: {path}", file=sys.stderr)
                if skipped is not None:
                    skipped.append(os.path.relpath(path, vault))
                continue
            pages.append({
                "name": fn[:-3],
                "dir": d,
                "path": os.path.relpath(path, vault),
                "type": fm.get("type", ""),
                "title": fm.get("title", fn[:-3]),
                "updated": fm.get("updated", ""),
                "confidence": fm.get("confidence", ""),
                "superseded": _is_superseded(fm.get("superseded_by", "")),
                "supersede_target_exists": _target_exists(
                    vault, fm.get("superseded_by", ""),
                    self_rel=os.path.relpath(path, vault)),
                "sources_n": (lambda s: 0 if not s else s.count(",") + 1)(fm.get("sources", "").strip()),
                "summary": summary_line(text),
            })
    return pages


def build(vault):
    pages = collect(vault)
    today = dt.date.today()
    out = ["# Brain Index", "",
           "<!-- DERIVED ARTIFACT — regenerated by scripts/rebuild_index.py."
           " Never hand-edit. -->",
           f"<!-- generated: {today.isoformat()} | pages: {len(pages)} -->", ""]
    for ptype, heading in SECTIONS:
        out.append(f"## {heading}")
        rows = [p for p in pages if p["type"] == ptype and not p["superseded"]]
        rows = sorted(rows, key=lambda x: x["name"])
        elided = 0
        if ptype == "digest":
            cutoff = today - dt.timedelta(days=DIGEST_WINDOW_DAYS)
            recent_rows = []
            for p in rows:
                try:
                    if dt.date.fromisoformat(p["updated"]) >= cutoff:
                        recent_rows.append(p)
                except ValueError:
                    recent_rows.append(p)  # undated: keep, don't hide silently
            elided = len(rows) - len(recent_rows)
            rows = recent_rows
        for p in rows:
            meta = f"(sources: {p['sources_n']}, updated {p['updated']}, conf: {p['confidence']})"
            out.append(f"- [[{p['name']}]] — {p['summary']} {meta}")
        if elided:
            out.append(f"- _plus {elided} digests older than {DIGEST_WINDOW_DAYS} days, "
                       f"in `concepts/` and searchable by date._")
        out.append("")
    superseded = [p for p in pages if p["superseded"]]
    if superseded:
        out.append("## Superseded (historical — follow superseded_by)")
        for p in sorted(superseded, key=lambda x: x["name"]):
            out.append(f"- [[{p['name']}]] ({p['type']})")
        out.append("")
    out.append(f"## Recent Changes (Last {RECENT_DAYS} Days)")
    cutoff = today - dt.timedelta(days=RECENT_DAYS)
    recent = []
    for p in pages:
        try:
            if dt.date.fromisoformat(p["updated"]) >= cutoff:
                recent.append(p)
        except ValueError:
            pass
    for p in sorted(recent, key=lambda x: x["updated"], reverse=True):
        out.append(f"- {p['updated']}: [[{p['name']}]] ({p['type']})")
    out.append("")
    return "\n".join(out)


def check_types(vault):
    """Folder-is-the-category assertion. Returns a list of (path, type, allowed).

    Deliberately NOT folded into --check. Lint rule 4 is index drift and its
    remedy is 'rebuild the index', which cannot clear a type mismatch; giving
    the two failures one exit code would route a reader to the wrong fix.
    """
    bad = []
    skipped = []
    for p in collect(vault, skipped=skipped):
        # A superseded page is a redirect: it sits at its OLD path on purpose so
        # inbound links still resolve (§5). Judging it against its folder would flag
        # every retirement the schema itself prescribes. But the exemption requires a
        # REAL target — a redirect to a page that does not exist is not a redirect, it
        # is an escape hatch, so those are gated like any other page.
        if p["superseded"] and p.get("supersede_target_exists"):
            continue
        allowed = FOLDER_TYPES.get(p["dir"])
        if allowed is not None and p["type"] not in allowed:
            bad.append((p["path"], p["type"], sorted(allowed)))
    return bad, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=os.path.expanduser("~/wiki"))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--check-types", action="store_true",
                    help="assert every page's type is allowed in its folder; exit 3 on mismatch")
    args = ap.parse_args()
    if args.check_types:
        bad, skipped = check_types(args.vault)
        for path, got, allowed in bad:
            print(f"type-mismatch: {path} has type {got!r}, folder allows {allowed}")
        for path in skipped:
            print(f"no-frontmatter: {path} is in a scanned folder and will never "
                  f"reach the index")
        if bad or skipped:
            if bad:
                print(f"{len(bad)} page(s) contradict their folder")
            if skipped:
                print(f"{len(skipped)} page(s) are silently absent from the index")
            sys.exit(3)
        print("types: no mismatches, no unindexable pages")
        return
    index_path = os.path.join(args.vault, "_system", "index.md")
    new = build(args.vault)
    if args.check:
        old = open(index_path, encoding="utf-8").read() if os.path.exists(index_path) else ""
        # Ignore the generated-date comment line when diffing
        strip = lambda t: [l for l in t.splitlines() if not l.startswith("<!-- generated:")]
        diff = list(difflib.unified_diff(strip(old), strip(new), "committed", "regenerated", lineterm=""))
        if diff:
            print("\n".join(diff))
            sys.exit(1)
        print("index: no drift")
        return
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"index rebuilt: {index_path}")


if __name__ == "__main__":
    main()
