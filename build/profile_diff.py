"""Compare the frozen profile (format 1) with a rebuilt one (format 2): which trusted sites change.

usage: profile_diff.py <old_profile.pkl> <new_profile.pkl> [--src work/sqlglot]

Dictionary entries: trusted in old and untrusted in new, or trusted in both with a different set
of readers (new readers = tests the old profile would have dropped). Comparison sites: for the
site's own operator family (is/== or is not/!=), the tests the old profile marked as able to tell a
swap apart versus the tests the new operand facts imply; sites where the new set is larger were
judged too safe before.
"""
import ast
import pickle
import sys
import zlib
from pathlib import Path

R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
import selector as S  # noqa: E402


def load(p):
    raw = Path(p).read_bytes()
    try:
        raw = zlib.decompress(raw)
    except zlib.error:
        pass
    return pickle.loads(raw)


def pc(m):
    return bin(m).count("1")


def main():
    old, new = load(sys.argv[1]), load(sys.argv[2])
    src = Path(sys.argv[sys.argv.index("--src") + 1]) if "--src" in sys.argv else R / "work" / "sqlglot"
    ALL = (1 << len(new["tests"])) - 1
    print(f"new profile: unobserved tests {pc(new.get('unobserved', 0))}, exact-dict checks {len(new.get('exact_checks', []))}, "
          f"collisions {len(new.get('collisions', []))}")
    for c in new.get("collisions", [])[:10]:
        print("  collision", c)
    lost, grew, shrank, same, n_old = [], [], [], 0, 0
    for owner, o in old["keys"].items():
        n = new["keys"].get(owner)
        for i, m in enumerate(o["mask"]):
            if m is None:
                continue
            n_old += 1
            nm = None if n is None or i >= len(n["mask"]) else n["mask"][i]
            if nm is None:
                lost.append((owner, i))
            elif nm & ~m:
                grew.append((owner, i, pc(nm & ~m)))
            elif m & ~nm:
                shrank.append((owner, i, pc(m & ~nm)))
            else:
                same += 1
    print(f"dictionary entries trusted before: {n_old}; now untrusted {len(lost)}, more readers {len(grew)}, "
          f"fewer readers {len(shrank)}, unchanged {same}")
    for x in grew[:15]:
        print("  more readers", x)
    for x in lost[:15]:
        print("  now untrusted", x)
    for x in shrank[:5]:
        print("  fewer readers", x)

    trees = {}
    worse, better, same_c = [], [], 0
    for site, rec in new["cmp"].items():
        rel, line, col = site
        if rel not in trees:
            trees[rel] = ast.parse((src / rel).read_text(encoding="utf-8"))
        node = next((n for n in ast.walk(trees[rel]) if isinstance(n, ast.Compare) and n.lineno == line and n.col_offset == col), None)
        if node is None or len(node.ops) != 1:
            continue
        negated = isinstance(node.ops[0], (ast.IsNot, ast.NotEq))
        if not all(S.Hunter._swap_preserves(f, negated) for f in rec["imp"]):
            nbad = ALL
        else:
            nbad = 0
            for f, m in rec["facts"].items():
                if not S.Hunter._swap_preserves(f, negated):
                    nbad |= m
        ob = old["cmp"].get(site)
        obad = ob[1] if ob else ALL
        if nbad & ~obad:
            worse.append((site, ast.unparse(node)[:60], pc(nbad & ~obad), pc(obad), pc(nbad)))
        elif obad & ~nbad:
            better.append((site, pc(obad & ~nbad)))
        else:
            same_c += 1
    print(f"comparison sites: {len(worse)} now keep more tests (judged too safe before), {len(better)} keep fewer, {same_c} unchanged")
    for w in worse[:20]:
        print("  too safe before", w)


if __name__ == "__main__":
    main()
