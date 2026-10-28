#!/usr/bin/env python3
"""git seance - commune with the code your repository buried.

Walks git history for functions that were deleted and never came back,
then lets each one speak, through a local open-weight model, about how it
lived and who killed it. Standard library only. By default the only network
call is to Ollama on localhost; SEANCE_PROVIDER=openai opts into a gateway.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

def _load_env(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")):
    """Read KEY=VALUE lines from a .env beside this script. Real env vars win."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_env()
PROVIDER = os.environ.get("SEANCE_PROVIDER", "ollama")      # ollama | openai
API_BASE = os.environ.get("SEANCE_API_BASE", "").rstrip("/")
API_KEY = os.environ.get("SEANCE_API_KEY", "")
OLLAMA_URL = os.environ.get("SEANCE_OLLAMA", "http://localhost:11434")
MODEL = os.environ.get("SEANCE_MODEL", "gemma3:4b")
NUM_GPU = os.environ.get("SEANCE_NUM_GPU", "")              # Ollama layers on GPU; 0 = CPU only

# A line that starts a definition, per language. Group 1 is the name.
DEF_PATTERNS = [
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),            # python
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)\s*\("),  # js/ts
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*\("),        # go
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),  # rust
]
COMMIT_MARK = "\x1eCOMMIT\x1f"
CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".go", ".rs")
SKIP_NAMES = {"main", "init", "__init__", "setUp", "tearDown", "test"}


@dataclass
class Ghost:
    name: str
    path: str
    body: list[str]
    killed_sha: str
    killed_by: str
    killed_on: str
    epitaph: str            # the commit message that deleted it
    born_on: str = "unknown"
    born_by: str = "unknown"
    lines: int = field(init=False)

    def __post_init__(self):
        self.lines = len(self.body)


def git(repo: str, *args: str) -> str:
    out = subprocess.run(["git", "-C", repo, *args], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def def_name(line: str) -> str | None:
    for pat in DEF_PATTERNS:
        m = pat.match(line)
        if m:
            return m.group(1)
    return None


def def_head(line: str) -> str:
    """`def name` without the signature, which may have changed over its life."""
    for pat in DEF_PATTERNS:
        m = pat.match(line)
        if m:
            return line[:m.end(1)].strip()
    return line.strip()


def indent(line: str) -> int:
    expanded = line.expandtabs(4)
    return len(expanded) - len(expanded.lstrip())


def parse_log(log: str) -> list[Ghost]:
    """Turn `git log -p -U0` output into candidate ghosts, newest death first."""
    ghosts: list[Ghost] = []
    for chunk in log.split(COMMIT_MARK)[1:]:
        header, _, diff = chunk.partition("\n")
        sha, author, date, subject = (header.split("\x1f") + ["", "", "", ""])[:4]
        path = None
        block: list[str] = []

        def flush():
            if block:
                name = def_name(block[0])
                if name and (path or "").endswith(CODE_EXT) and name not in SKIP_NAMES and len(block) >= 2:
                    ghosts.append(Ghost(name, path or "?", list(block), sha[:8],
                                        author, date, subject))
                block.clear()

        for line in diff.splitlines():
            if line.startswith("--- "):
                flush()
                p = line[4:]
                path = p[2:] if p.startswith("a/") else p
            elif line.startswith("-") and not line.startswith("---"):
                text = line[1:]
                starts_def = def_name(text) is not None
                if block and starts_def and indent(text) <= indent(block[0]):
                    flush()           # a sibling or outer def ends the current body
                if block or starts_def:
                    block.append(text)
            else:
                flush()
        flush()
    return ghosts


def still_alive(repo: str, name: str) -> bool:
    out = subprocess.run(["git", "-C", repo, "grep", "-qwE",
                          rf"(def|function|func|fn)\s+(\([^)]*\)\s*)?{re.escape(name)}"],
                         capture_output=True)
    return out.returncode == 0


def birth(repo: str, g: Ghost) -> None:
    # --full-history: without it, a path that later moved can vanish behind a merge
    out = git(repo, "log", "--full-history", "--reverse", "--format=%ad\x1f%an", "--date=short",
              f"-S{def_head(g.body[0])}", "--", g.path).strip().splitlines()
    if out:
        g.born_on, g.born_by = (out[0].split("\x1f") + ["unknown"])[:2]


def find_ghosts(repo: str, max_commits: int) -> list[Ghost]:
    log = git(repo, "log", "-p", "-U0", "--no-color", "--no-merges", "-M",
              f"-n{max_commits}", "--date=short",
              f"--format={COMMIT_MARK}%H\x1f%an\x1f%ad\x1f%s")
    seen, dead = set(), []
    for g in parse_log(log):
        key = (g.name, g.path)
        if key in seen:
            continue
        seen.add(key)
        if not still_alive(repo, g.name):
            dead.append(g)
    return dead


# ---------------------------------------------------------------- the medium

SYSTEM = """You are the ghost of a function that was deleted from a codebase.
Speak in first person, as the function, from beyond the grave. You are wistful,
a little dramatic, darkly funny, never cruel. Refer to real details you are given:
your own code, the date you were born, the commit that killed you and its message,
the person who deleted you. Never invent facts about the code that are not shown.
Call people by their name or "they"; never guess anyone's gender.
Keep it under 120 words. No headings, no lists. End with one line of advice to the
living developers."""


def prompt_for(g: Ghost, question: str | None) -> str:
    body = "\n".join(g.body[:60])
    q = f"\nThe living ask you: {question}" if question else \
        "\nTell the living who you were and how you died."
    return (f"You were `{g.name}` in `{g.path}`, {g.lines} lines long.\n"
            f"Born {g.born_on} by {g.born_by}. Deleted {g.killed_on} by {g.killed_by} "
            f"in commit {g.killed_sha}, whose message was: \"{g.epitaph}\".\n"
            f"Your final form:\n```\n{body}\n```{q}")


def ollama_stream(prompt: str):
    payload = {"model": MODEL, "stream": True, "messages": [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt}]}
    if NUM_GPU:
        payload["options"] = {"num_gpu": int(NUM_GPU)}
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:   # first call loads the model
        for raw in resp:
            if not raw.strip():
                continue
            msg = json.loads(raw)
            if "error" in msg:
                raise OSError(msg["error"])
            yield msg.get("message", {}).get("content", "")
            if msg.get("done"):
                return


def openai_stream(prompt: str):
    """Any OpenAI-compatible gateway, e.g. SoCLaaS serving an open-weight Qwen."""
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=json.dumps({"model": MODEL, "stream": True, "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt}]}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                return
            event = json.loads(data)
            if "error" in event:
                err = event["error"]
                raise OSError(err.get("message", err) if isinstance(err, dict) else err)
            choices = event.get("choices") or [{}]
            yield (choices[0].get("delta") or {}).get("content") or ""


def planchette(g: Ghost) -> str:
    """The offline medium: no model, just the facts, arranged ominously."""
    lines = [
        f"I was {g.name}. {g.lines} lines. I lived in {g.path}.",
        f"On {g.killed_on}, {g.killed_by} came for me.",
        f"They wrote only this: \"{g.epitaph}\".",
        "Nobody calls me now. Nobody imports me.",
        "Check your callers before you delete. Some of us had friends.",
    ]
    return " ".join(lines)


def speak(text_iter, slow: bool) -> None:
    buf = ""
    for piece in text_iter:
        for ch in piece:
            sys.stdout.write(ch)
            sys.stdout.flush()
            buf += ch
            if slow:
                time.sleep(0.012 if ch not in ".,…" else 0.18)
    print()


def channel(g: Ghost, question: str | None, offline: bool, slow: bool) -> None:
    dim, reset = ("\033[2m", "\033[0m") if sys.stdout.isatty() else ("", "")
    print(f"\n{dim}🕯  summoning {g.name}  ·  {g.path}  ·  "
          f"{g.born_on} – {g.killed_on}  ·  killed in {g.killed_sha}{reset}\n")
    if not offline:
        try:
            stream = openai_stream if PROVIDER == "openai" else ollama_stream
            speak(stream(prompt_for(g, question)), slow)
            return
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            if isinstance(e, urllib.error.HTTPError):   # e.g. Ollama's "model not found"
                e = f"{e}: {e.read(300).decode('utf-8', 'replace').strip()}"
            print(f"{dim}(the medium is silent: {e}. reading the planchette instead){reset}\n")
    speak(textwrap.fill(planchette(g), 78), slow)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="git seance",
                                 description="Talk to the functions your repo deleted.")
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("-n", "--commits", type=int, default=2000, help="history depth")
    ap.add_argument("--list", action="store_true", help="show the graveyard, summon nobody")
    ap.add_argument("--summon", metavar="NAME", help="call one ghost by name")
    ap.add_argument("--ask", metavar="QUESTION", help="ask the ghost something")
    ap.add_argument("--offline", action="store_true", help="no model, facts only")
    ap.add_argument("--fast", action="store_true", help="skip the dramatic typing")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):   # Windows pipes default to cp1252 and choke on 🪦
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    ghosts = find_ghosts(a.repo, a.commits)
    if not ghosts:
        print("The graveyard is empty. Either this repo is young, or nobody here ever lets go.")
        return 0
    if a.list:
        print(f"🪦  {len(ghosts)} function{'' if len(ghosts)==1 else 's'} rest{'s' if len(ghosts)==1 else ''} here:\n")
        for g in ghosts:
            print(f"  {g.killed_on}  {g.name:<30} {g.lines:>4} lines  {g.path}  ({g.killed_by})")
        return 0
    if a.summon:
        matches = [g for g in ghosts if g.name == a.summon]
        if not matches:
            print(f"No spirit named {a.summon} answers.")
            return 1
        g = matches[0]
    else:
        g = random.choice(ghosts)
    birth(a.repo, g)
    channel(g, a.ask, a.offline, not a.fast)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
