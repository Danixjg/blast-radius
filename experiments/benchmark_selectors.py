"""Build isolated copies and compare fresh selector processes without overlapping timings."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import resource
import shutil
import statistics
import subprocess
import tarfile
import time
import zlib


def selector_environment(data):
    env = dict(os.environ, BR_DATA=str(data), PYTHONDONTWRITEBYTECODE="1")
    for key in ("BR_MAP", "BR_SRC"):
        env.pop(key, None)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default="/usr/bin/python3.13")
    parser.add_argument("--repeats", type=int, default=11)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.baseline = args.baseline.resolve()
    args.candidate = args.candidate.resolve()
    args.output = args.output.resolve()
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("--output must be a fresh empty directory to preserve earlier evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    kit = args.output / "kit"
    kit.mkdir(exist_ok=True)
    with tarfile.open(args.baseline / "blast-radius-sample.tar.gz") as archive:
        archive.extractall(kit, filter="data")
    variants = {}
    env = selector_environment(kit / "sample")
    tested_python = subprocess.check_output(
        [args.python, "-S", "-c", "import platform; print(platform.python_version())"],
        text=True, env=env).strip()
    for name in ("baseline", "optimized", "prepared"):
        root = args.output / name
        root.mkdir(exist_ok=True)
        source = args.baseline if name == "baseline" else args.candidate
        shutil.copy2(source / "selector.py", root / "selector.py")
        with tarfile.open(source / "src.tar.gz") as archive:
            archive.extractall(root / "src", filter="data")
        # Match Docker's build-time decompression for both pickle variants.
        (root / "map.pkl").write_bytes(zlib.decompress((source / "map.pkl").read_bytes()))
        if name == "prepared":
            code = ("import selector; "
                    "selector.MAP.write_bytes(selector.serialize_map(selector.load_map()))")
            subprocess.run([args.python, "-S", "-c", code], cwd=root, env=env, check=True)
        subprocess.run([args.python, "-m", "compileall", "-q", str(root)], check=True)
        variants[name] = root
    rows, expected = [], None
    order = list(variants)
    rng = random.Random(4107)
    for iteration in range(args.repeats + 1):
        rng.shuffle(order)
        for name in order:
            root = variants[name]
            before = resource.getrusage(resource.RUSAGE_CHILDREN)
            start = time.perf_counter()
            run = subprocess.run([args.python, "-S", str(root / "selector.py")],
                                 capture_output=True, env=env, check=True)
            wall = time.perf_counter() - start
            after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu = after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime
            output = root / f"output-{iteration}.jsonl"
            output.write_bytes(run.stdout)
            (root / f"stderr-{iteration}.log").write_bytes(run.stderr)
            parsed = [json.loads(line) for line in run.stdout.splitlines()]
            canonical = {x["commit"]: sorted(n for worker in x["workers"] for n in worker)
                         for x in parsed}
            if expected is None:
                expected = canonical
            if canonical != expected:
                raise RuntimeError(f"coverage differs: {name}")
            score = subprocess.run([args.python, str(kit / "score.py"), str(kit / "sample"),
                                    str(kit / "sample/answers.json"), str(output)],
                                   capture_output=True, text=True, check=True)
            if "Simulated: 121.100 s" not in score.stdout or "Misses:    0" not in score.stdout:
                raise RuntimeError(f"unexpected official sample score: {name}: {score.stdout}")
            rows.append(dict(variant=name, iteration=iteration, warmup=iteration == 0,
                             cpu_seconds=cpu, wall_seconds=wall,
                             output_sha256=hashlib.sha256(run.stdout).hexdigest()))
    # Demonstrate copies can run together, separately from isolated performance results.
    concurrent_start = time.perf_counter()
    copies = [subprocess.Popen([args.python, "-S", str(root / "selector.py")], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
              for root in variants.values()]
    for copy in copies:
        stdout, stderr = copy.communicate(timeout=120)
        if copy.returncode:
            raise RuntimeError(stderr.decode())
        canonical = {x["commit"]: sorted(n for worker in x["workers"] for n in worker)
                     for x in map(json.loads, stdout.splitlines())}
        if canonical != expected:
            raise RuntimeError("concurrent copy changed coverage")
    summary = {}
    for name in variants:
        measured = [row for row in rows if row["variant"] == name and not row["warmup"]]
        summary[name] = {key: statistics.median(row[key] for row in measured)
                         for key in ("cpu_seconds", "wall_seconds")}
        summary[name]["map_bytes"] = (variants[name] / "map.pkl").stat().st_size
    result = dict(python=tested_python, harness_python=platform.python_version(), executable=args.python,
                  cpu_limit=Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
                  repeats=args.repeats, warmups=1, seed=4107, summary=summary, runs=rows,
                  concurrent_smoke=dict(copies=len(copies),
                                        wall_seconds=time.perf_counter()-concurrent_start),
                  invariant="identical selected test sets; official sample scorer 121.100s, zero misses")
    (args.output / "selector-results.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
