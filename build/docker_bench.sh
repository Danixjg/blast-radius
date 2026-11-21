#!/bin/bash
# Official-limits Docker runs with whole-container CPU, scored by the kit's scorer.
# usage: build/docker_bench.sh <image> <data_dir> <answers.json> [runs=3]
#
# Each run starts a fresh container with the scorer's flags. The selector runs as usual and, after
# it exits, the container's own cgroup reports usage_usec (user+system of every process in the
# container) on stderr. The run's stdout is scored with --cpu-seconds, and the middle total of the
# runs is printed like the board computes it.
set -euo pipefail
IMAGE=$1; DATA=$(cd "$2" && pwd); ANSWERS=$3; RUNS=${4:-3}
KIT=$(cd "$(dirname "$0")/.." && pwd)/kit
OUT=$(mktemp -d)
FLAGS=(--rm --platform linux/amd64 --network none --cpus 2 --memory 4096m --memory-swap 4096m
       --pids-limit 256 --read-only --tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL
       --security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp
       --volume "$DATA/commits:/data/commits:ro" --volume "$DATA/tests.json:/data/tests.json:ro")
RUNCMD='python -S -c "import sys; sys.path.insert(0, \"/app\"); import selector; selector.main()"; rc=$?; echo "CGROUP $(grep usage_usec /sys/fs/cgroup/cpu.stat)" >&2; exit $rc'
totals=()
for i in $(seq 1 "$RUNS"); do
  start=$(date +%s.%N)
  docker run "${FLAGS[@]}" "$IMAGE" sh -c "$RUNCMD" > "$OUT/run$i.jsonl" 2> "$OUT/run$i.err"
  wall=$(echo "$(date +%s.%N) - $start" | bc)
  usec=$(grep -o 'usage_usec [0-9]*' "$OUT/run$i.err" | awk '{print $2}')
  cpu=$(echo "scale=6; $usec / 1000000" | bc)
  bytes=$(wc -c < "$OUT/run$i.jsonl")
  echo "run $i: wall ${wall}s cpu ${cpu}s stdout ${bytes} bytes"
  python3 "$KIT/score.py" "$DATA" "$ANSWERS" "$OUT/run$i.jsonl" --cpu-seconds "$cpu" | tee "$OUT/run$i.score"
  totals+=("$(grep '^Total:' "$OUT/run$i.score" | awk '{print $2}')")
done
printf '%s\n' "${totals[@]}" | sort -n | awk '{a[NR]=$1} END {print "median total:", a[int((NR+1)/2)]}'
echo "outputs in $OUT"
