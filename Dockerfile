# The scorer builds this on linux/amd64 and runs it with no network. The digest is the
# image the organisers labelled every commit in; the selector itself needs only the stdlib.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# map.pkl     - per-test coverage map of sqlglot at eb11aec4 (built by build/, see README)
# profile.pkl - runtime profile: literal-dictionary entry reads and is/== agreement per test
# sqlglot itself is fetched from its own repository at the pinned commit during the build and
# verified file by file against build/sqlglot.manifest; its MIT LICENSE is kept at /app/src/LICENSE.
COPY selector.py map.pkl profile.pkl /app/
COPY build/fetch_sqlglot.py build/sqlglot.manifest /tmp/fetch/

RUN python -I /tmp/fetch/fetch_sqlglot.py /app/src /tmp/fetch/sqlglot.manifest \
 && rm -rf /tmp/fetch \
 && python -c "import zlib, pathlib; [p.write_bytes(zlib.decompress(p.read_bytes())) for p in map(pathlib.Path, ('/app/map.pkl', '/app/profile.pkl'))]" \
 && python -m compileall -q /app/src/sqlglot /app/selector.py \
 && chmod -R a+rX /app

ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-S", "-c", "import sys; sys.path.insert(0, '/app'); import selector; selector.main()"]
