#!/bin/bash
# Simulates the container: packaged /app layout, no site-packages, clean env, read-only app.
set -e
R=$(cd "$(dirname "$0")/.." && pwd)
W=${WORK:-$R/work}; K=${KIT:-$R/kit}
APP=$W/appsim/app
PY=$(readlink -f ~/.local/bin/python3.13)
chmod -R u+w $W/appsim 2>/dev/null || true
rm -rf $W/appsim && mkdir -p $APP
cp $R/selector.py $R/map.pkl $R/src.tar.gz $APP/
cd $APP
$PY -c "import tarfile; tarfile.open('$APP/src.tar.gz').extractall('$APP/src', filter='data')"
rm $APP/src.tar.gz
$PY -c "import zlib; p='$APP/map.pkl'; d=zlib.decompress(open(p,'rb').read()); open(p,'wb').write(d)"
$PY -m compileall -q $APP/src/sqlglot $APP/selector.py
chmod -R a-w $APP
DATA=${1:-$K/sample}
for i in 1 2 3; do
  env -i HOME=/tmp PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 BR_DATA=$DATA /usr/bin/time -f "%U user %S sys %e wall" $PY -S -c "import sys; sys.path.insert(0, '$APP'); import selector; selector.main()" > $W/sim.jsonl 2> $W/sim.err
  tail -1 $W/sim.err
done
grep -i "check\|catcher\|error" $W/sim.err | head || true
