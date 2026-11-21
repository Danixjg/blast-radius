"""Fetch sqlglot at the pinned commit from its own GitHub repository and verify it file by file.

usage (Docker build):   python fetch_sqlglot.py <dest_dir> <manifest>
usage (maintainers):    python fetch_sqlglot.py --make-manifest <pinned_checkout> <manifest>

The archive GitHub serves for a commit is not promised to be byte-identical over time, so the
check is on content: every file the selector needs (sqlglot/**, tests/**/*.py, pyproject.toml and
the upstream LICENSE) must match the SHA-256 recorded in the committed manifest, and nothing else
is extracted. Any mismatch, missing file or network failure after retries fails the build.
Standard library only.
"""
import hashlib
import io
import os
import sys
import tarfile
import time
import urllib.request

SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
URLS = [f"https://codeload.github.com/tobymao/sqlglot/tar.gz/{SHA}",
        f"https://github.com/tobymao/sqlglot/archive/{SHA}.tar.gz"]


def wanted(rel):
    return (rel.startswith("sqlglot/") and "/__pycache__/" not in rel and not rel.endswith((".pyc", ".so"))) or \
        (rel.startswith("tests/") and rel.endswith(".py")) or rel in ("LICENSE", "pyproject.toml")


def make_manifest(tree, out):
    rows = []
    for dirpath, dirnames, fnames in os.walk(tree):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for fn in sorted(fnames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, tree).replace(os.sep, "/")
            if wanted(rel):
                with open(full, "rb") as f:
                    rows.append(f"{hashlib.sha256(f.read()).hexdigest()}  {rel}")
    rows.sort(key=lambda r: r.split("  ", 1)[1])
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(f"# sqlglot {SHA}: sha256 of every file the selector reads\n")
        f.write("\n".join(rows) + "\n")
    print(f"{len(rows)} files -> {out}")


def download():
    last = None
    for attempt in range(6):
        for url in URLS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "blast-radius-build"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read()
            except Exception as e:   # retry transient network errors
                last = e
                print(f"download failed ({url}): {e!r}", file=sys.stderr)
        time.sleep(min(30, 2 ** attempt))
    raise SystemExit(f"could not download sqlglot {SHA}: {last!r}")


def fetch(dest, manifest):
    expect = {}
    with open(manifest, encoding="utf-8") as f:
        for ln in f:
            if ln.strip() and not ln.startswith("#"):
                digest, rel = ln.rstrip("\n").split("  ", 1)
                expect[rel] = digest
    blob = download()
    got = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as t:
        for m in t.getmembers():
            if not m.isfile():
                continue
            parts = m.name.split("/", 1)
            if len(parts) != 2 or not parts[0].startswith("sqlglot-"):
                continue
            rel = parts[1]
            if not wanted(rel):
                continue
            data = t.extractfile(m).read()
            if rel not in expect:
                raise SystemExit(f"unexpected file in archive: {rel}")
            if hashlib.sha256(data).hexdigest() != expect[rel]:
                raise SystemExit(f"content mismatch: {rel}")
            got[rel] = data
    missing = sorted(set(expect) - set(got))
    if missing:
        raise SystemExit(f"{len(missing)} files missing from archive, e.g. {missing[:3]}")
    for rel, data in got.items():
        path = os.path.join(dest, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    print(f"sqlglot {SHA}: {len(got)} files verified -> {dest}")


if __name__ == "__main__":
    if sys.argv[1] == "--make-manifest":
        make_manifest(sys.argv[2], sys.argv[3])
    else:
        fetch(sys.argv[1], sys.argv[2])
