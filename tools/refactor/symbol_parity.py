#!/usr/bin/env python3
"""Split-parity check: new package surface must equal the captured baseline.

Usage: python tools/refactor/symbol_parity.py <baseline.txt> <new.dotted.path>
Baseline lines = top-level names (non-module, non-dunder) of the original
module, captured with capture_symbols before the split. Exit 0 = identical.
"""
import importlib
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))


def surface(mod):
    return {n for n, v in vars(mod).items()
            if not n.startswith('__') and not isinstance(v, types.ModuleType)}


def main():
    baseline = set(Path(sys.argv[1]).read_text(encoding='utf-8').split())
    new = surface(importlib.import_module(sys.argv[2]))
    for n in sorted(baseline - new):
        print('MISSING', n)
    for n in sorted(new - baseline):
        print('EXTRA', n)
    return 0 if not (baseline - new) and not (new - baseline) else 1


if __name__ == '__main__':
    sys.exit(main())
