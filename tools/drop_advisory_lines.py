# SPDX-License-Identifier: Apache-2.0
"""Keep declaration lines whose descriptor kind is not advisory.

A line is ``owner/repo@ref/path.json``. ``ref`` is a release tag or a
40-character commit. The descriptor is fetched and its ``kind`` is read.
``advisory`` lines are dropped. Nothing in this tool names an evaluator.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

LINE = re.compile(
    r"^(?P<owner>[A-Za-z0-9][A-Za-z0-9-]*)/(?P<repo>[A-Za-z0-9._-]+)@"
    r"(?P<ref>v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)|[0-9a-f]{40})/"
    r"(?P<path>[A-Za-z0-9._/-]+\.json)$"
)
SHA = re.compile(r"^[0-9a-f]{40}$")


class DropError(RuntimeError):
    pass


def _token() -> str:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "verification-reports-action"})
    token = _token()
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
        request.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise DropError(f"{url} was refused: HTTP {exc.code}") from exc


def read_url(url: str, fetch=fetch_bytes) -> bytes:
    """Read ``url``. A refused request is a :class:`DropError` that names it."""
    try:
        return fetch(url)
    except urllib.error.HTTPError as exc:
        raise DropError(f"{url} was refused: HTTP {exc.code}") from exc
    except DropError:
        raise


def peel_commit(owner: str, repo: str, ref: str, fetch=fetch_bytes) -> str:
    if SHA.fullmatch(ref):
        return ref
    url = f"https://api.github.com/repos/{owner}/{repo}/git/ref/tags/{ref}"
    payload = json.loads(read_url(url, fetch))
    obj = payload.get("object") if isinstance(payload, dict) else None
    if not isinstance(obj, dict):
        raise DropError(f"{owner}/{repo} tag {ref} did not peel to a commit")
    sha = obj.get("sha")
    kind = obj.get("type")
    if kind == "tag":
        tag_url = f"https://api.github.com/repos/{owner}/{repo}/git/tags/{sha}"
        tag = json.loads(read_url(tag_url, fetch))
        inner = tag.get("object") if isinstance(tag, dict) else None
        sha = inner.get("sha") if isinstance(inner, dict) else None
        kind = inner.get("type") if isinstance(inner, dict) else None
    if kind != "commit" or not isinstance(sha, str) or SHA.fullmatch(sha) is None:
        raise DropError(f"{owner}/{repo} tag {ref} did not peel to a commit")
    return sha


def descriptor_kind(line: str, fetch=fetch_bytes) -> str:
    match = LINE.fullmatch(line.strip())
    if match is None:
        raise DropError(f"{line!r} is not a declaration line")
    owner, repo, ref, path = match.group("owner", "repo", "ref", "path")
    if any(part in {".", ".."} for part in path.split("/")):
        raise DropError(f"{line!r} is not a declaration line")
    commit = peel_commit(owner, repo, ref, fetch)
    url = f"https://raw.githubusercontent.com/{owner}/{repo}/{commit}/{path}"
    document = json.loads(read_url(url, fetch))
    if not isinstance(document, dict):
        raise DropError(f"{line} descriptor is not an object")
    kind = document.get("kind")
    if not isinstance(kind, str) or not kind:
        raise DropError(f"{line} descriptor has no kind")
    return kind


def keep_lines(text: str, fetch=fetch_bytes) -> tuple[list[str], int]:
    kept = []
    dropped = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip().strip("'\"")
        if descriptor_kind(line, fetch) == "advisory":
            dropped += 1
            continue
        kept.append(line)
    return kept, dropped


def write_github_output(count: int, text: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    delimiter = f"ghadelim_{os.urandom(8).hex()}"
    body = text if text.endswith("\n") or text == "" else text + "\n"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"count={count}\n")
        handle.write(f"lines<<{delimiter}\n{body}{delimiter}\n")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: drop_advisory_lines.py LINES OUT", file=sys.stderr)
        return 2
    source = Path(args[0]).read_text(encoding="utf-8")
    try:
        kept, dropped = keep_lines(source)
    except (DropError, OSError, json.JSONDecodeError, urllib.error.URLError) as exc:
        print(f"advisory lines: {exc}", file=sys.stderr)
        return 1
    text = "\n".join(kept) + ("\n" if kept else "")
    Path(args[1]).write_text(text, encoding="utf-8")
    write_github_output(dropped, text)
    print(dropped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
