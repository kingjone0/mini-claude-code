"""Search the historical P1 *exact* pending-verdict sentence with explicit scope.

This is an audit of obsolete source text, not the runtime verdict gate. The latter
uses a broader regex against state rows and must remain unchanged.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Construct in segments so this scanner does not itself contain the old sentence.
NEEDLE = "待 harness " + "独立 pytest 判定"
IMPLEMENTATION = ("eval/harness", "eval/lib", "coding_agent.py", "agent")
DOCUMENTS = ("eval/reports",)


def files_in(scope: str):
    paths = IMPLEMENTATION + (DOCUMENTS if scope == "all" else ())
    for relative in paths:
        path = ROOT / relative
        if path.is_file():
            yield path
        elif path.is_dir():
            yield from sorted(path.rglob("*.py" if relative != "eval/reports" else "*.md"))


def scan(scope: str):
    matches = []
    for path in files_in(scope):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NEEDLE in line:
                category = "报告引文" if "eval/reports/" in path.relative_to(ROOT).as_posix() else "实现"
                matches.append((path.relative_to(ROOT).as_posix(), number, category, line))
    return matches


def main(argv=None):
    parser = argparse.ArgumentParser(description="P1 旧判定占位句作用域扫描")
    parser.add_argument("--scope", choices=("implementation", "all"), required=True)
    args = parser.parse_args(argv)
    matches = scan(args.scope)
    print(f"scope={args.scope} matches={len(matches)}")
    for path, number, category, line in matches:
        print(f"{path}:{number} [{category}] {line}")
    implementation_hits = sum(category == "实现" for _, _, category, _ in matches)
    print(f"implementation_hits={implementation_hits} document_quotes={len(matches)-implementation_hits}")
    return 2 if implementation_hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
