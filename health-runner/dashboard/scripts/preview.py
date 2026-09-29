#!/usr/bin/env python3
"""Render the real template from synthetic data or an explicitly supplied JSON snapshot."""
import argparse
import csv
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_dashboard as build


def render(data, as_of):
    build.presentation_data(data, as_of)
    data["tiles"] = build.tiles(data["weight"], data["weight7"], data["bp"], data["sleep"],
                               data["rhr"], data["steps"], data["injections"], as_of,
                               hrv=data["hrv"], energy=data.get("energy", []))
    template = build.read_template(ROOT / "template.html")
    if template.count("/*__DATA__*/") != 1:
        raise ValueError("Expected exactly one data marker")
    return template.replace("/*__DATA__*/", json.dumps(data).replace("</", "<\\/"))


def refresh_training(data, repository):
    """Recompute training from an explicitly selected read-only local CSV source."""
    repository = repository.resolve()
    sources = {}
    for name in ('sessions', 'sets', 'cardio'):
        path = (repository / 'data' / (name + '.csv')).resolve()
        if path.is_relative_to('/mnt') or not path.is_relative_to(repository):
            raise ValueError('Training files must remain inside the native Linux repository')
        with path.open(newline='') as handle:
            sources['data/' + name + '.csv'] = list(csv.DictReader(handle))
    prior = data.get('training_detail', {})
    with patch.object(build, 'rows', side_effect=lambda path: sources[path]):
        data['training'] = build.training_daily(data.get('workouts', []))
        data['training_detail'] = build.training_detail(data.get('workouts', []), data['training'],
            {'snapshots': prior.get('prescriptions', []), 'errors': prior.get('prescription_errors', [])})
    data['training_types'] = build.TRAINING_ORDER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="Private JSON snapshot; omitted for fabricated test data")
    parser.add_argument("--training-log", type=Path, help="Recompute training from this local repository; requires --snapshot")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Override the snapshot's Central build date")
    parser.add_argument("--output", type=Path, required=True, help="Local HTML destination; must be gitignored for private snapshots")
    args = parser.parse_args()
    if args.training_log and not args.snapshot:
        parser.error("--training-log requires --snapshot")
    output = args.output.resolve()
    if output.is_relative_to('/mnt') or (args.snapshot and args.snapshot.resolve().is_relative_to('/mnt')):
        parser.error("Use native Linux paths for previews")
    if args.snapshot:
        ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", str(output)], capture_output=True)
        if ignored.returncode:
            parser.error("Private previews must be written to a gitignored path in this repository (for example design/review/index.html)")
        data = json.loads(args.snapshot.read_text())
    else:
        sys.path.insert(0, str(ROOT / "tests"))
        from dashboard_fixture import snapshot
        data = snapshot()
    as_of = args.as_of or date.fromisoformat(data["meta"]["built_at_ct"][:10])
    if args.training_log:
        refresh_training(data, args.training_log)
    html = render(data, as_of)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html)
    print("Preview written locally; selected training CSVs were read without writes." if args.training_log else "Preview written locally; canonical sources were not accessed.")


if __name__ == "__main__":
    main()
