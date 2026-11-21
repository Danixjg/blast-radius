# The scorer builds this on linux/amd64 and runs it with no network. The digest is the
# image the organisers labelled every commit in; the selector itself needs only the stdlib.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

# map.pkl  - per-test coverage map of sqlglot at eb11aec4 (built by build/, see README)
# src.tar.gz - sqlglot's own sources and test modules at that commit (MIT licensed)
COPY selector.py map.pkl src.tar.gz /app/

# Opt in while measuring: preserve the original runtime map as the default.
ARG MAP_FORMAT=pickle

RUN python -c "import tarfile; tarfile.open('/app/src.tar.gz').extractall('/app/src', filter='data')" \
 && rm /app/src.tar.gz \
 && python -c "import zlib; p='/app/map.pkl'; d=zlib.decompress(open(p,'rb').read()); open(p,'wb').write(d)" \
 && if [ "$MAP_FORMAT" = indexed ]; then python -c "import sys; sys.path.insert(0, '/app'); import selector; selector.MAP.write_bytes(selector.serialize_map(selector.load_map()))"; elif [ "$MAP_FORMAT" != pickle ]; then exit 2; fi \
 && python -m compileall -q /app/src/sqlglot /app/selector.py \
 && chmod -R a+rX /app

ENV PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-S", "-c", "import sys; sys.path.insert(0, '/app'); import selector; selector.main()"]
