"""Write the pinned source's reverse import sites for the selector, without coverage masks.

Usage: python -S build_import_index.py SELECTOR SOURCE OUTPUT [--if-supported]
"""
import argparse
import importlib.util
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--if-supported", action="store_true", help="allow benchmarking older selectors")
    args = parser.parse_args()
    program, source, output = args.program, args.source, args.output
    spec = importlib.util.spec_from_file_location("selector", program)
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    if args.if_supported and not hasattr(getattr(selector, "Hunter", None), "build_import_site_index"):
        return
    selector.SRC = source.resolve()
    empty_map = {"tests": [], "lines": {}, "classes": {}, "imp_lines": {}, "opens": {}, "subproc": 0}
    index = selector.Hunter(empty_map).build_import_site_index()
    if "sqlglot" not in index["sites"]:
        raise SystemExit("no sqlglot import sites in pristine sources")
    record = {"version": selector.IMPORT_SITE_INDEX_VERSION, "source": str(selector.SRC), **index}
    output.write_text(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n", encoding="utf-8")
    print(f"indexed {sum(map(len, index['sites'].values()))} import sites across {len(index['sites'])} module "
          f"names, {len(index['prefix'])} prefix and {len(index['any'])} unbounded dynamic imports")


if __name__ == "__main__":
    main()
