# The scorer builds this on linux/amd64 and runs it with no network. The digest is the
# image the organisers labelled every commit in; the selector itself needs only the stdlib.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# map.pkl     - per-test coverage map of sqlglot at eb11aec4 (built by build/, see README)
# profile.pkl - runtime profile: literal-dictionary entry reads and is/== agreement per test
# order-boundaries.json records exact-integer comparison observations from the same pinned suite.
# sqlglot itself is fetched from its own repository at the pinned commit during the build and
# verified file by file against build/sqlglot.manifest; its MIT LICENSE is kept at /app/src/LICENSE.
# build/verify-requirements.txt pins (by sha256) the labelling environment's versions of the few
# pure-Python packages the verifier's test runs need, so those runs take the labelled code paths.
COPY selector.py map.pkl profile.pkl probe_imports.json order-boundaries.json /app/
COPY build/fetch_sqlglot.py build/sqlglot.manifest build/verify-requirements.txt /tmp/fetch/
COPY build/build_import_index.py /tmp/fetch/

RUN python -I /tmp/fetch/fetch_sqlglot.py /app/src /tmp/fetch/sqlglot.manifest \
 && python -B -S /tmp/fetch/build_import_index.py /app/selector.py /app/src /app/import-sites.json \
 && python -m pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes --disable-pip-version-check --root-user-action=ignore -r /tmp/fetch/verify-requirements.txt \
 && rm -rf /tmp/fetch \
 && python -c "import zlib, pathlib; [p.write_bytes(zlib.decompress(p.read_bytes())) for p in map(pathlib.Path, ('/app/map.pkl', '/app/profile.pkl'))]" \
 && python -m compileall -q -x '(^|[^A-Za-z0-9_])(site-packages|tests?|idle_test)([^A-Za-z0-9_]|$)' /usr/local/lib/python3.13 \
 && python -m compileall -q /app/src/sqlglot /app/src/tests /app/selector.py \
 && chmod -R a+rX /app

ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-S", "-c", "import sys; sys.path.insert(0, '/app'); import selector; selector.main()"]
