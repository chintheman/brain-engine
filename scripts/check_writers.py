#!/usr/bin/env python3
"""check_writers.py — assert every vault commit since the cutover is attributable.

BRAIN.md §0.2 allows exactly two writers of schema'd pages. In practice many
processes commit to the vault, and a census on 2026-09-11 found 22 distinct commit
prefixes plus a class carrying none at all. Since every commit shares one git
author, the subject prefix is the only attribution signal available.

This asserts that every commit made AFTER the declared cutover carries either a §8
prefix or a prefix declared in <vault>/_system/writers.yaml. History before the
cutover is immutable (§8 forbids rewriting it) and is deliberately out of scope —
a check that judges unfixable history can never pass, and a check that can never
pass gets ignored, which is the failure mode this whole exercise exists to fix.

Usage:
  check_writers.py [--vault ~/wiki] [--since ISO8601]   # default: registry cutover
  check_writers.py --negative-control                    # must FAIL, proving it can

Exit: 0 conformant, 1 violations found, 2 usage/config error.
"""
import argparse
import os
import re
import subprocess
import sys

SANCTIONED = ("deposit", "dream", "lint", "capture")  # BRAIN.md §8


def load_registry(vault):
    path = os.path.join(vault, "_system", "writers.yaml")
    if not os.path.exists(path):
        print(f"ERROR: no writer registry at {path}", file=sys.stderr)
        sys.exit(2)
    try:
        import yaml
        data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    except Exception as exc:
        print(f"ERROR: registry unreadable: {exc}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, dict):
        print(f"ERROR: registry is not a mapping: {path}", file=sys.stderr)
        sys.exit(2)
    writers = data.get("writers") or []
    if not isinstance(writers, list):
        print(f"ERROR: registry `writers` is not a list: {path}", file=sys.stderr)
        sys.exit(2)
    prefixes = [(str(w["prefix"]), str(w.get("delimiter", "colon")))
                for w in writers if isinstance(w, dict) and w.get("prefix")]
    if not prefixes:
        print(f"ERROR: registry declares no writers: {path}", file=sys.stderr)
        sys.exit(2)
    pin = data.get("cutover_commit")
    # A pin that is YAML-null, an all-zero OID, or not a hex sha is NOT a pin. Saying
    # so out loud matters: silently falling back to the date filter is exactly the
    # forgeable path the pin exists to replace.
    pin_s = "" if pin is None else str(pin).strip().strip('"').strip("'")
    if pin_s and (not re.fullmatch(r"[0-9a-fA-F]{7,40}", pin_s)
                  or set(pin_s) <= {"0"}):
        print(f"WARN: cutover_commit {pin_s!r} is not a usable sha; "
              f"falling back to the date filter", file=sys.stderr)
        pin_s = ""
    return prefixes, data.get("cutover"), pin_s


def git(vault, *args):
    return subprocess.run(["git", "-C", vault, *args],
                          capture_output=True, text=True).stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", default=os.path.expanduser("~/wiki"))
    ap.add_argument("--since")
    ap.add_argument("--negative-control", action="store_true",
                    help="run against a date before the cutover; must report violations")
    args = ap.parse_args()

    prefixes, cutover, registry_base = load_registry(args.vault)
    since = args.since or ("2026-07-13" if args.negative_control else cutover)
    if not since:
        print("ERROR: registry has no cutover and --since not given", file=sys.stderr)
        sys.exit(2)

    # A declared prefix matches only with its declared delimiter. Colon is the
    # default; a bare space is opt-in, because matching any prefix followed by a
    # space would let ordinary prose ("fix the index by hand") pass as declared.
    decl = list(prefixes) + [(p, "colon") for p in SANCTIONED]
    alts = []
    for name, delim in sorted(set(decl), key=lambda x: -len(x[0])):
        esc = re.escape(name)
        if delim == "space":
            # A bare `prefix \S` match let any prose beginning with the prefix pass.
            # A space-delimited writer emits a machine token, not English: require a
            # token with no lowercase word characters followed by a space.
            alts.append(esc + r" [0-9A-Za-z._:+-]+$")
        elif delim == "token":
            # For a MULTI-WORD attributed prefix: accept `prefix: text`, `prefix <token...>: text`,
            # or `prefix <anything to end of subject>`.
            #
            # Why the last, loosest form is needed: one real subject was
            # `context discipline G9 live; hook reads SubagentHandback report` -- no colon
            # anywhere, so neither the colon form (rejects text before the colon) nor the
            # space form (requires a machine token running to the END) can ever match it.
            # Four commits sat red for a week with no declaration able to clear them.
            #
            # THIS IS DELIBERATELY LOOSER THAN `space` AND IS THE ONE WIDENING THAT OPENS A
            # HOLE: any subject beginning with this exact phrase passes, e.g.
            # "context discipline is a good idea". That is acceptable ONLY because the phrase
            # is multi-word and attributed to a specific writer, so unrelated prose is not
            # expected to open with it. Never declare `token` for a single common word
            # (`fix`, `log`, `task`) -- that is what `space` and `colon` are for.
            #
            # REVIEW REFINEMENT: the third branch was ` .+$`, which matched ANY subject merely
            # opening with the phrase -- "auto-sync I am not the auto-sync writer, deleting the
            # vault" passed and was attributed to a writer holding may_touch ["**"]. What gets
            # laundered is the attribution and the write right, not the phrase, so the tail now
            # has to look like a write: a colon or a semicolon within 60 characters. Every real
            # subject carries one ("context discipline G9: ...", "auto-sync 2026-10-09--1639:
            # unblock ...", "context discipline G9 live; hook reads ..."); free prose does not.
            alts.append(esc + r"(: | .{0,60}[;:] )")
        else:
            alts.append(esc + r"(\([^)]*\))?: ")
    pat = re.compile("^(" + "|".join(alts) + ")")

    # --since filters by author date, which a backdated commit at HEAD can use to
    # prune the entire range (observed: six post-cutover commits vanished, exit 2
    # "nothing proved"). Prefer a topological walk from the cutover commit.
    # A SHA pins the range to the DAG. --before is a date filter, and one
    # `git commit --date=<pre-cutover>` at HEAD collapsed the whole range to empty,
    # which the watchdog then read as "nothing to check".
    # An explicit --since always wins: silently auditing the pinned range while
    # reporting the user's date was a false PASS over a range nobody asked for.
    # The negative control must look BEFORE the cutover, so it never uses the pin.
    use_pin = not args.negative_control and not args.since
    base = (registry_base or "").strip() if use_pin else ""

    # Lens 1 #2: `git log A..HEAD` does not error when A is not an ancestor — it
    # silently becomes a symmetric difference and can reach back past the cutover.
    if base:
        anc = subprocess.run(["git", "-C", args.vault, "merge-base",
                              "--is-ancestor", base, "HEAD"],
                             capture_output=True, text=True)
        if anc.returncode != 0:
            print(f"WARN: cutover_commit {base[:12]} is not an ancestor of HEAD "
                  f"(rebase or reset?); falling back to the date filter",
                  file=sys.stderr)
            base = ""
    if not base:
        base = git(args.vault, "rev-list", "-1", f"--before={since}", "HEAD").strip()
    if base:
        log = git(args.vault, "log", f"{base}..HEAD", "--pretty=format:%h %s")
    else:
        log = git(args.vault, "log", f"--since={since}", "--pretty=format:%h %s")
    lines = [l for l in log.splitlines() if l.strip()]
    if not lines:
        print(f"FAIL: no commits since {since} — nothing proved")
        sys.exit(2)

    bad = [l for l in lines if not pat.match(l.split(" ", 1)[1] if " " in l else "")]

    if args.negative_control:
        if bad:
            print(f"negative control OK: {len(bad)} undeclared of {len(lines)} "
                  f"since {since} — the check can fail")
            for l in bad[:5]:
                print(f"  {l}")
            sys.exit(0)
        print("negative control FAILED: nothing flagged before the cutover, "
              "so this check proves nothing")
        sys.exit(1)

    if bad:
        print(f"FAIL: {len(bad)} of {len(lines)} commits since {since} "
              f"carry no declared prefix:")
        for l in bad:
            print(f"  {l}")
        sys.exit(1)
    print(f"PASS: {len(lines)} commits since {since}, every one declared")


if __name__ == "__main__":
    main()
