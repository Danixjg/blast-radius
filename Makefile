IMAGE ?= blast-radius-entry
MAP_FORMAT ?= pickle
KIT ?= $(CURDIR)/kit
SAMPLE ?= $(KIT)/sample
OUTPUT := $(CURDIR)/output.jsonl
WORK ?= $(CURDIR)/work
SQLGLOT_SHA := eb11aec4be8cb2122afe713a4816662c3f0fb2fa
PY := $(WORK)/venv/bin/python

RUN_FLAGS := --rm --platform linux/amd64 --network none --cpus 2 \
	--memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
	--tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
	--security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
	--volume $(SAMPLE)/commits:/data/commits:ro \
	--volume $(SAMPLE)/tests.json:/data/tests.json:ro

.PHONY: build sample check kit map local

$(KIT)/score.py:
	mkdir -p $(KIT) && tar xzf blast-radius-sample.tar.gz -C $(KIT)

kit: $(KIT)/score.py

build:
	docker build --platform linux/amd64 --build-arg MAP_FORMAT=$(MAP_FORMAT) --tag $(IMAGE) .

sample: kit build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 $(KIT)/score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT)

check: kit build
	docker run $(RUN_FLAGS) $(IMAGE) > $(OUTPUT)
	python3 $(KIT)/score.py $(SAMPLE) $(SAMPLE)/answers.json $(OUTPUT) > /dev/null && echo "format ok"

# Without docker: run the selector on the sample from a packaged copy, then score.
local: kit
	bash build/simulate.sh $(SAMPLE)
	python3 $(KIT)/score.py $(SAMPLE) $(SAMPLE)/answers.json $(WORK)/sim.jsonl

# Regenerate map.pkl and src.tar.gz from the pinned sqlglot.
map: kit
	test -d $(WORK)/sqlglot || git clone -q https://github.com/tobymao/sqlglot.git $(WORK)/sqlglot
	cd $(WORK)/sqlglot && git checkout -q $(SQLGLOT_SHA)
	test -x $(PY) || (uv venv -q -p 3.13 $(WORK)/venv && VIRTUAL_ENV=$(WORK)/venv uv pip install -q -r $(KIT)/requirements.lock)
	rm -rf $(WORK)/cov
	for rev in 0 1; do \
	  (cd $(WORK)/sqlglot && PATH=$(WORK)/venv/bin:$$PATH SKIP_INTEGRATION=0 PYTHONPATH=.:$(CURDIR)/build \
	   COV_ROOT=$(WORK)/sqlglot COV_OUT=$(WORK)/cov COV_REVERSE=$$rev \
	   python -m pytest -q -p no:cacheprovider -p covplugin -n auto tests | tail -1); \
	done
	$(PY) build/mkmap.py $(WORK)/cov $(WORK)/sqlglot $(SAMPLE)/tests.json map.pkl
	cd $(WORK)/sqlglot && tar --owner=0 --group=0 --mode=a+rX --exclude=__pycache__ -czf $(CURDIR)/src.tar.gz \
	  sqlglot $$(find tests -name "*.py" | sort)
