"""Run the Dockerfile's own COPY and RUN instructions outside Docker, under a scratch prefix.

usage: emulate_build.py <python3.13> [--keep DIR] [--worktree]

Exports the committed tree (git archive HEAD; --worktree: the working tree), then executes each
COPY (files only) and the RUN
chain with every absolute path rewritten under a temporary root and `python` replaced by the
given interpreter, timing the whole thing. It checks that the commands themselves work (download,
manifest verification, decompression, byte-compilation) where Docker is unavailable. It does not
check the base image, users or permissions; a real `docker build` remains the reference.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

R = Path(__file__).resolve().parent.parent


def main():
    py = sys.argv[1]
    keep = sys.argv[sys.argv.index("--keep") + 1] if "--keep" in sys.argv else None
    root = Path(keep) if keep else Path(tempfile.mkdtemp(prefix="br-build-"))
    ctx = root / "context"
    shutil.rmtree(root, ignore_errors=True)
    ctx.mkdir(parents=True)
    if "--worktree" in sys.argv:
        # the working tree as the build context (tracked and untracked, not ignored): checks a
        # candidate before it is committed
        names = subprocess.run(["git", "-C", str(R), "ls-files", "-co", "--exclude-standard", "-z"],
                               check=True, capture_output=True).stdout.decode().split("\0")
        for n in filter(None, names):
            src = R / n
            if src.is_file():
                (ctx / n).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, ctx / n)
    else:
        arc = subprocess.run(["git", "-C", str(R), "archive", "HEAD"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(ctx)], input=arc, check=True)
    text = (ctx / "Dockerfile").read_text(encoding="utf-8")
    instrs, cur = [], ""
    for ln in text.splitlines():
        if ln.strip().startswith("#") and not cur:
            continue
        cur += ln.rstrip("\\").rstrip() + " " if ln.rstrip().endswith("\\") else ln
        if not ln.rstrip().endswith("\\"):
            if cur.strip():
                instrs.append(cur.strip())
            cur = ""
    fs = root / "fs"

    def host(p):
        return str(fs) + p if p.startswith("/") else p

    t0 = time.monotonic()
    for ins in instrs:
        op, _, rest = ins.partition(" ")
        if op == "COPY":
            *srcs, dst = rest.split()
            d = Path(host(dst))
            d.mkdir(parents=True, exist_ok=True)
            for s in srcs:
                shutil.copy2(ctx / s, d / Path(s).name)
        elif op == "RUN":
            cmd = re.sub(r"(?<![\w.])(/(?:app|tmp)\b[^\s'\",)]*)", lambda m: host(m.group(1)).replace("\\", "/"), rest)
            cmd = re.sub(r"(^|&&\s*)python\b", lambda m: m.group(1) + py.replace("\\", "/"), cmd)
            print("RUN", cmd[:160], "...")
            subprocess.run(["bash", "-ec", cmd], check=True)
    secs = time.monotonic() - t0
    app = fs / "app"
    size = sum(p.stat().st_size for p in app.rglob("*") if p.is_file())
    print(f"emulated build OK in {secs:.1f}s; /app holds {size / 1e6:.1f} MB: {sorted(p.name for p in app.iterdir())}")
    if not keep:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
