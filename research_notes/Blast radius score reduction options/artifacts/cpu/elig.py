
import sys, json, os
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import selector as S
S.SRC = Path(os.environ['BR_SRC']); S.BUDGET_S = 1e6
data = S.load_map(); h = S.Hunter(data, S.load_profile(data['tests']))
out = {'unverifiable': [], 'why': {}}
bad_sub = data['subproc']
for i, t in enumerate(data['tests']):
    if h.unverifiable >> i & 1:
        out['unverifiable'].append(t)
        w = []
        if bad_sub >> i & 1: w.append('subproc')
        f = t.split('::')[0]
        if f in S.VERIFY_UNSAFE_FILES: w.append('unsafe_file')
        if t in S.VERIFY_UNSAFE_TESTS: w.append('unsafe_test')
        if not w: w.append('opens')
        out['why'][t] = w
sets = {}
for name, d in zip(sys.argv[2::2], sys.argv[3::2]):
    res = {}
    for p in sorted(Path(d, 'commits').glob('*.patch')):
        mask, probe = h.analyze(p.read_bytes().decode('utf-8', 'surrogateescape'))
        res[p.stem] = {'vf': h.verify_files is not None, 'all': bool(mask) and mask == h.ALL, 'probe': probe is not None,
                       'files': sorted({(fp.old or fp.new) for fp in S.parse_patch(p.read_bytes().decode('utf-8','surrogateescape'))}) if hasattr(S,'parse_patch') else []}
    sets[name] = res
out['sets'] = sets
json.dump(out, open(sys.argv[-1] if False else os.environ['OUT'], 'w'))
print('ok', len(out['unverifiable']))
