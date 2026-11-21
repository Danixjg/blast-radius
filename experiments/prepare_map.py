"""Prepare an optional coverage-equivalent indexed map at build time."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selector


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.destination.resolve():
        parser.error("keep the original map; choose a different destination")
    selector.MAP = args.source
    data = selector.load_map()
    encoded = selector.serialize_map(data)
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    args.destination.write_bytes(encoded)
    selector.MAP = args.destination
    if selector.load_map() != data:
        raise RuntimeError("prepared map failed exact round-trip validation")
    print(f"Prepared and verified {len(encoded)} bytes at {args.destination}")


if __name__ == "__main__":
    main()
