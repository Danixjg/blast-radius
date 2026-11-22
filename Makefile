IMAGE ?= blast-radius-entry
KIT ?= $(CURDIR)/kit
SAMPLE ?= $(KIT)/sample
OUTPUT := $(CURDIR)/output.jsonl
WORK ?= $(CURDIR)/work
SQLGLOT_SHA := eb11aec4be8cb2122afe713a4816662c3f0fb2fa
PY := $(WORK)/venv/bin/python
SHELL := /bin/bash
.SHELLFLAGS := -o pipefail -ec

RUN_FLAGS := --rm --platform linux/amd64 --network none --cpus 2 \
	--memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
	--tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
	--security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
	--volume $(SAMPLE)/commits:/data/commits:ro \
	--volume $(SAMPLE)/tests.json:/data/tests.json:ro

.PHONY: build sample measure check kit map profile local test checkout venv holdout eval

$(KIT)/score.py:
	mkdir -p $(KIT) && tar xzf blast-radius-sample.tar.gz -C $(KIT)

kit: $(KIT)/score.py

build:
	docker build --platform linux/amd64 --tag $(IMAGE) .

# One official-limits run, scored by the kit's own scorer (simulated seconds and misses).
sample: kit build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 $(KIT)/score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT)

# Three fresh official-limits containers with whole-container CPU from the cgroup; median total.
measure: kit build
	bash build/docker_bench.sh $(IMAGE) $(SAMPLE) $(SAMPLE)/answers.json 3

check: kit build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 $(KIT)/score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT) > /dev/null && echo "format ok"

# The pinned checkout everything local uses (LF line endings regardless of git settings).
checkout:
	test -d $(WORK)/sqlglot || git -c core.autocrlf=false clone -q https://github.com/tobymao/sqlglot.git $(WORK)/sqlglot
	git -C $(WORK)/sqlglot config core.autocrlf false
	git -C $(WORK)/sqlglot checkout -q $(SQLGLOT_SHA)
	test -z "$$(git -C $(WORK)/sqlglot status --porcelain --untracked-files=no)"

venv: kit
	test -x $(PY) || (uv venv -q -p 3.13 $(WORK)/venv && VIRTUAL_ENV=$(WORK)/venv uv pip install -q -r $(KIT)/requirements.lock)

# Without docker: lay out /app like the image (from the pinned checkout), run 3 times with CPU
# of the whole process tree measured, and score each run with the kit's scorer.
local: kit checkout
	$(PY) build/bench.py app $(WORK)/apps/candidate --src $(WORK)/sqlglot --profile profile.pkl
	$(PY) build/bench.py run $(WORK)/apps/candidate $(SAMPLE) $(SAMPLE)/answers.json --runs 3 --label candidate --out $(WORK)/bench

test: checkout venv
	$(PY) -m unittest discover -s build -p 'test_*.py' -v
	$(PY) -m unittest discover -s tests -v

# Rebuild map.pkl / profile.pkl. Every coverage pass must exit 0 with every catalogued test
# reporting and none failing, and every test must be traced, or nothing is replaced.
map: kit checkout venv
	$(PY) build/buildmaps.py --what map

profile: kit checkout venv
	$(PY) build/buildmaps.py --what profile
	$(PY) build/fetch_sqlglot.py --make-manifest $(WORK)/sqlglot build/sqlglot.manifest

# A fresh, independently labelled holdout (labels stay in work/, outside the image).
holdout: kit checkout venv
	$(PY) build/holdout.py $(WORK)/sqlglot $(WORK)/holdout
	test -d $(WORK)/label-tree || git -C $(WORK)/sqlglot worktree add -q --detach ../label-tree $(SQLGLOT_SHA)
	$(PY) build/label.py $(WORK)/label-tree $(SAMPLE)/tests.json $(WORK)/holdout/labels $(WORK)/holdout/commits/*.patch
	$(PY) build/answers.py $(WORK)/holdout

# Every labelled set (sample, holdouts, mutants, work/sets/*), 3 fresh runs each, one table in
# work/eval/table.json. BASELINE=<git ref> adds the same runs for selector/map/profile at that ref.
eval: kit checkout venv
	$(PY) build/evalsets.py $(if $(BASELINE),--baseline $(BASELINE))
