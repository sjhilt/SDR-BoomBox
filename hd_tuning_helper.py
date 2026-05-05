"""
Standalone HD Radio tuning helper for SDR-BoomBox.

This script helps you manually test gain/PPM combinations with nrsc5 and ffplay,
then record which settings held sync best.
"""

from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
NRSC5 = BASE_DIR / 'nrsc5.exe'
FFPLAY = BASE_DIR / 'ffplay.exe'
LOT_DIR = Path.home() / '.sdr_boombox_data'
RESULTS_FILE = BASE_DIR / 'hd_tuning_results.json'


@dataclass
class TestResult:
    frequency_mhz: float
    hd_program: int
    gain: float
    ppm: int
    verdict: str
    notes: str
    timestamp: float


def parse_float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(',') if part.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(',') if part.strip()]


def build_cmd(freq: float, program: int, gain: float, ppm: int, device_index: int, use_rtltcp: bool, rtltcp_host: str) -> str:
    source = f'-H "{rtltcp_host}"' if use_rtltcp else f'-d {device_index}'
    return (
        f'& "{NRSC5}" {source} -p {ppm} -g {gain} --dump-aas-files "{LOT_DIR}" -t wav -o - {freq} {program} '
        f'| & "{FFPLAY}" -nodisp -i pipe:0'
    )


def launch_test(freq: float, program: int, gain: float, ppm: int, device_index: int, use_rtltcp: bool, rtltcp_host: str) -> None:
    if not NRSC5.exists():
        raise SystemExit(f'nrsc5.exe not found at {NRSC5}')
    if not FFPLAY.exists():
        raise SystemExit(f'ffplay.exe not found at {FFPLAY}')
    LOT_DIR.mkdir(exist_ok=True)
    cmd = build_cmd(freq, program, gain, ppm, device_index, use_rtltcp, rtltcp_host)
    print('\nRunning test:')
    print(cmd)
    print('\nA new PowerShell window will open. Listen for stability, then close that window when done.\n')
    subprocess.run([
        'powershell', '-NoExit', '-Command', cmd,
    ], check=False)


def load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def save_result(result: TestResult) -> None:
    results = load_results()
    results.append(asdict(result))
    RESULTS_FILE.write_text(json.dumps(results, indent=2), encoding='utf-8')


def print_summary() -> None:
    results = load_results()
    if not results:
        print('No saved tuning results yet.')
        return
    rank = {'best': 3, 'good': 2, 'mixed': 1, 'bad': 0}
    results = sorted(results, key=lambda item: (rank.get(item['verdict'], -1), item['timestamp']), reverse=True)
    print('\nSaved results:')
    for item in results:
        print(
            f"- {item['frequency_mhz']:.1f} MHz HD{item['hd_program'] + 1} | gain {item['gain']:.1f} | ppm {item['ppm']} | {item['verdict'].upper()}"
            + (f" | {item['notes']}" if item.get('notes') else '')
        )


def interactive(args: argparse.Namespace) -> None:
    gains = parse_float_list(args.gains)
    ppms = parse_int_list(args.ppms)
    tests = list(itertools.product(gains, ppms))
    print(f'Prepared {len(tests)} tests for {args.frequency:.1f} MHz HD{args.program + 1}.')
    print('Results will be saved to:', RESULTS_FILE)

    for index, (gain, ppm) in enumerate(tests, start=1):
        print(f'\n[{index}/{len(tests)}] Gain {gain:.1f} / PPM {ppm}')
        launch_test(args.frequency, args.program, gain, ppm, args.device_index, args.use_rtltcp, args.rtltcp_host)
        verdict = input('Verdict [best/good/mixed/bad/skip/q]: ').strip().lower()
        if verdict in {'q', 'quit'}:
            break
        if verdict == 'skip' or not verdict:
            continue
        if verdict not in {'best', 'good', 'mixed', 'bad'}:
            print('Invalid verdict, skipping save for this test.')
            continue
        notes = input('Optional notes: ').strip()
        save_result(TestResult(
            frequency_mhz=args.frequency,
            hd_program=args.program,
            gain=gain,
            ppm=ppm,
            verdict=verdict,
            notes=notes,
            timestamp=time.time(),
        ))

    print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(description='Standalone HD Radio gain/PPM tuning helper for SDR-BoomBox')
    parser.add_argument('--frequency', type=float, default=103.7, help='Station frequency in MHz')
    parser.add_argument('--program', type=int, default=0, help='HD subchannel number: 0=HD1, 1=HD2, etc.')
    parser.add_argument('--gains', default='14.4,19.7,28.0,36.4', help='Comma-separated gain candidates')
    parser.add_argument('--ppms', default='0', help='Comma-separated PPM candidates')
    parser.add_argument('--device-index', type=int, default=0, help='RTL-SDR device index')
    parser.add_argument('--use-rtltcp', action='store_true', help='Use rtl_tcp instead of direct USB')
    parser.add_argument('--rtltcp-host', default='127.0.0.1', help='rtl_tcp host when --use-rtltcp is set')
    parser.add_argument('--summary', action='store_true', help='Show saved results and exit')
    args = parser.parse_args()

    if args.summary:
        print_summary()
        return

    interactive(args)


if __name__ == '__main__':
    main()
