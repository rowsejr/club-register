"""Check the repository for words that must never appear in it, and for personal data.

The words themselves are not stored here: only their SHA-256 hashes, in tools/forbidden.sha256, so the list cannot leak.
Every tracked file name, every line of every tracked text file and every commit message is split into lower-case words;
each word, and each run of two and three words, is hashed and compared with the list.

    python tools/scan.py                       check everything (exit code 1 if anything is found)
    python tools/scan.py --staged              check only what is about to be committed (the pre-commit hook)
    python tools/scan.py --make-hashes FILE    turn a private list of words (one per line) into tools/forbidden.sha256
"""

from __future__ import annotations

import hashlib
import re
import subprocess  # noqa: S404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HASHES = ROOT / "tools" / "forbidden.sha256"
TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".sql", ".sh", ".gs", ".ts", ".cfg", ".ini", ".example", ".csv"}
ALLOWED_EMAIL_DOMAINS = ("example.org", "example.com", "example.net", ".test", ".example", ".invalid", "margateyachtclub.co.uk", "localhost", "anthropic.com")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
UK_MOBILE = re.compile(r"(?<!\d)07\d{3}\s?\d{3}\s?\d{3}(?!\d)")
WORD = re.compile(r"[a-z0-9]+")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _phrases(text: str) -> set[str]:
    words = WORD.findall(text.lower())
    out = set(words)
    out.update(" ".join(words[i:i + 2]) for i in range(len(words) - 1))
    out.update(" ".join(words[i:i + 3]) for i in range(len(words) - 2))
    out.update("".join(words[i:i + 3]) for i in range(len(words) - 2))  # "Club Manager" written as one word
    out.update("".join(words[i:i + 2]) for i in range(len(words) - 1))
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True, encoding="utf-8").stdout  # noqa: S603, S607


def _files(staged: bool) -> list[str]:
    if staged:
        return [f for f in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if f]
    return [f for f in _git("ls-files").splitlines() if f]


def scan(staged: bool = False) -> list[str]:
    forbidden = {line.strip() for line in HASHES.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
    problems: list[str] = []
    for name in _files(staged):
        if any(_hash(p) in forbidden for p in _phrases(name)):
            problems.append(f"{name}: the file name uses a forbidden word")
        path = ROOT / name
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", ".gitignore", ".env.example"}:
            continue
        if not path.exists() or name == "tools/forbidden.sha256":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            if any(_hash(p) in forbidden for p in _phrases(line)):
                problems.append(f"{name}:{number}: a forbidden word")
            if "scan: allow" in line:  # a deliberately real-looking test value
                continue
            for match in EMAIL.finditer(line):
                domain = match.group(1).lower()
                if not domain.endswith(ALLOWED_EMAIL_DOMAINS) and not domain.endswith((".png", ".jpg", ".svg")):
                    problems.append(f"{name}:{number}: an email address that is not made up ({match.group(0)})")
            for match in UK_MOBILE.finditer(line):
                if not match.group(0).replace(" ", "").startswith("07700900"):
                    problems.append(f"{name}:{number}: a phone number outside the made-up range")
    try:
        messages = [] if staged else _git("log", "--format=%B%x00").split("\x00")
    except subprocess.CalledProcessError:
        messages = []  # no commits yet
    for number, message in enumerate(messages, start=1):
        if any(_hash(p) in forbidden for p in _phrases(message)):
            problems.append(f"commit message #{number}: a forbidden word")
    return problems


def make_hashes(source: Path) -> None:
    words = sorted({" ".join(WORD.findall(line.lower())) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()})
    HASHES.write_text("# SHA-256 of words that must never appear in this repository (see tools/scan.py)\n" + "\n".join(sorted(_hash(w) for w in words if w)) + "\n", encoding="utf-8")
    print(f"Wrote {len(words)} hashes to {HASHES}")


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--make-hashes":
        make_hashes(Path(sys.argv[2]))
        return
    problems = scan(staged="--staged" in sys.argv)
    for problem in problems:
        print(problem)
    print(f"{len(problems)} problem(s)" if problems else "Clean")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
