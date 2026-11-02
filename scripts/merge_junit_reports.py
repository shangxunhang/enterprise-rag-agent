"""Merge independent pytest JUnit XML reports without losing provenance."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
import xml.etree.ElementTree as ET


def _number(value: str | None, *, integer: bool) -> int | float:
    if value is None or value == "":
        return 0 if integer else 0.0
    return int(value) if integer else float(value)


def _iter_suites(root: ET.Element):
    if root.tag == "testsuite":
        yield root
        return
    for suite in root.findall(".//testsuite"):
        if not suite.findall("./testsuite"):
            yield suite


def merge_junit_reports(inputs: list[Path], output: Path) -> dict[str, int | float]:
    if not inputs:
        raise ValueError("at least one JUnit input is required")
    merged_root = ET.Element("testsuites", name="stage_e_regression_v3")
    totals: dict[str, int | float] = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time": 0.0,
    }
    for source in inputs:
        if not source.exists():
            raise FileNotFoundError(source)
        root = ET.parse(source).getroot()
        suites = list(_iter_suites(root))
        if not suites:
            raise ValueError(f"no testsuite found in {source}")
        for suite in suites:
            copied = deepcopy(suite)
            copied.set("source_junit", str(source.resolve()))
            merged_root.append(copied)
            totals["tests"] += int(_number(suite.get("tests"), integer=True))
            totals["failures"] += int(
                _number(suite.get("failures"), integer=True)
            )
            totals["errors"] += int(_number(suite.get("errors"), integer=True))
            totals["skipped"] += int(
                _number(suite.get("skipped"), integer=True)
            )
            totals["time"] += float(_number(suite.get("time"), integer=False))
    for key in ("tests", "failures", "errors", "skipped"):
        merged_root.set(key, str(int(totals[key])))
    merged_root.set("time", f"{float(totals['time']):.6f}")
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged_root).write(
        output,
        encoding="utf-8",
        xml_declaration=True,
    )
    return totals


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("inputs", nargs="+")
    return parser


def main() -> int:
    args = _parser().parse_args()
    totals = merge_junit_reports(
        [Path(item).expanduser() for item in args.inputs],
        Path(args.output).expanduser(),
    )
    print(
        " ".join(
            [
                f"tests={int(totals['tests'])}",
                f"failures={int(totals['failures'])}",
                f"errors={int(totals['errors'])}",
                f"skipped={int(totals['skipped'])}",
                f"time={float(totals['time']):.6f}",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
