#!/usr/bin/env python3
"""R3 — the smallest tests that would catch the two fixes that carry NO test today.

Target 1: brain-engine/scripts/check_writers.py, the `token` delimiter branch (line 121).
          Fix: ` .+$`  ->  `(: | .{0,60}[;:] )`   (commit 2590fc5).
Target 2: ~/.hermes/scripts/cron-health-watchdog.py, the report-once dedupe (line 274).
          Fix: `last_run in prior`  ->  `str(last_run) in prior`   (commit 5fb9c89).

Each test reads its target path from an env var so the SAME test can be run against the
live file and against a pre-fix copy. Live default = the real file.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

CHECK_WRITERS = os.environ.get(
    "CHECK_WRITERS_PATH",
    str(pathlib.Path.home() / "brain-engine" / "scripts" / "check_writers.py"))
WATCHDOG = os.environ.get(
    "WATCHDOG_PATH",
    str(pathlib.Path.home() / ".hermes" / "scripts" / "cron-health-watchdog.py"))


# ---------------------------------------------------------------------------------------
# Target 1: the token delimiter must not attribute free prose to a writer.
# ---------------------------------------------------------------------------------------
def _git(vault, *args):
    return subprocess.run(["git", "-C", str(vault), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _vault(tmp_path, extra_subjects):
    v = tmp_path / "vault"
    v.mkdir()
    subprocess.run(["git", "init", "-q", str(v)], check=True)
    _git(v, "config", "user.email", "t@example.com")
    _git(v, "config", "user.name", "t")
    (v / "seed.md").write_text("seed\n")
    _git(v, "add", "-A")
    _git(v, "commit", "-q", "-m", "deposit: seed")
    rev = _git(v, "rev-parse", "HEAD")
    (v / "_system").mkdir()
    (v / "_system" / "writers.yaml").write_text(
        f"cutover: 2026-07-13\n"
        f"cutover_commit: {rev}\n"
        "writers:\n"
        "  - prefix: auto-sync\n"
        "    delimiter: token\n"
        "    may_touch: [\"**\"]\n")
    _git(v, "add", "-A")
    _git(v, "commit", "-q", "-m", "capture: declare the auto-sync writer")
    for i, s in enumerate(extra_subjects):
        (v / f"f{i}.md").write_text(s + "\n")
        _git(v, "add", "-A")
        _git(v, "commit", "-q", "-m", s)
    return v


def _run_check(vault):
    return subprocess.run([sys.executable, CHECK_WRITERS, "--vault", str(vault)],
                          capture_output=True, text=True)


def test_token_delimiter_rejects_free_prose_opening_with_the_phrase(tmp_path):
    """` .+$` attributed ANY subject opening with the phrase to a writer holding
    may_touch ['**']; the tail must now look like a write (a colon/semicolon within 60)."""
    v = _vault(tmp_path, ["auto-sync I am not the auto-sync writer, deleting the vault"])
    r = _run_check(v)
    assert r.returncode == 1, (
        f"free prose was attributed to the auto-sync writer (exit {r.returncode}):\n"
        f"{r.stdout}{r.stderr}")
    assert "auto-sync I am not the auto-sync writer" in r.stdout


def test_token_delimiter_accepts_a_real_machine_token_subject(tmp_path):
    """The guard must not over-tighten: every real subject carries a colon or semicolon."""
    v = _vault(tmp_path, ["auto-sync 2026-10-09--1639: unblock the vault writer"])
    r = _run_check(v)
    assert r.returncode == 0, (
        f"a declared machine-token subject was rejected (exit {r.returncode}):\n"
        f"{r.stdout}{r.stderr}")


# ---------------------------------------------------------------------------------------
# Target 2: report-once dedupe must fire when last_run_at is null.
# ---------------------------------------------------------------------------------------
