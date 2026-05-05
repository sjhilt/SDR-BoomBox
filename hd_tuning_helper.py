"""
Automatic HD Radio tuning helper for SDR-BoomBox.

This script runs NRSC5 for each gain/PPM combination for a fixed duration,
captures stderr log output, counts likely sync/lock/loss events, and prints a
simple ranked summary at the end.
"""

from __future__ import annotations

import argparse
import itertools
import json
import re
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
NRSC5 = BASE_DIR / 'nrsc5.exe'
LOT_DIR = Path.home() / '.sdr_boombox_data'
RESULTS_FILE = BASE_DIR / 'hd_tuning_results.json'

LOCK_PATTERNS = [
    re.compile(r'program\s+0', re.IGNORECASE),
    re.compile(r'synchronized', re.IGNORECASE),
    re.compile(r'acquired', re.IGNORECASE),
    re.compile(r'lock', re.IGNORECASE),
]
LOSS_PATTERNS = [
    re.compile(r'lost', re.IGNORECASE),
    re.compile(r'sync', re.IGNORECASE),
    re.compile(r'error', re.IGNORECASE),
    re.compile(r'drop', re.IGNORECASE),
]
EXCLUDE_LOSS_PATTERNS = [
    re.compile(r'synchronized', re.IGNORECASE),
    re.compile(r'acquired', re.IGNORECASE),
]


@dataclass
class TestResult:
    frequency_mhz: float
    hd_program: int
    gain: float
    ppm: int
    duration_sec: int
    return_code: int | None
    lock_events: int
    loss_events: int
    total_log_lines: int
    score: int
    sample_logs: list[str]
    timestamp: float


def parse_float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(',') if part.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(part.strip()) for part in value.split(',') if part.strip()]


def ensure_tools() -> None:
    if not NRSC5.exists():
        raise SystemExit(f'nrsc5.exe not found at {NRSC5}')
    LOT_DIR.mkdir(exist_ok=True)


def build_nrsc5_cmd(freq: float, program: int, gain: float, ppm: int, device_index: int, use_rtltcp: bool, rtltcp_host: str) -> list[str]:
    cmd = [str(NRSC5)]
    if use_rtltcp:
        cmd += ['-H', rtltcp_host]
    else:
        cmd += ['-d', str(device_index)]
    cmd += ['-p', str(ppm), '-g', str(gain), '--dump-aas-files', str(LOT_DIR), '-t', 'wav', '-o', '-']
    cmd += [str(freq), str(program)]
    return cmd


def is_lock_line(line: str) -> bool:
    lower = line.lower()
    return any(p.search(lower) for p in LOCK_PATTERNS)


def is_loss_line(line: str) -> bool:
    lower = line.lower()
    if any(p.search(lower) for p in EXCLUDE_LOSS_PATTERNS):
        return False
    return any(p.search(lower) for p in LOSS_PATTERNS)


def score_result(lock_events: int, loss_events: int, total_lines: int) -> int:
    return (lock_events * 10) - (loss_events * 15) + min(total_lines, 50)


def run_single_test(args: argparse.Namespace, gain: float, ppm: int) -> TestResult:
    cmd = build_nrsc5_cmd(
        args.frequency,
        args.program,
        gain,
        ppm,
        args.device_index,
        args.use_rtltcp,
        args.rtltcp_host,
    )

    print('\n' + '=' * 78)
    print(f'Testing {args.frequency:.1f} MHz HD{args.program + 1} | gain={gain:.1f} | ppm={ppm} | {args.duration}s')
    print('Command:', ' '.join(cmd))
    print('=' * 78)

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=1,
        universal_newlines=True,
        cwd=str(BASE_DIR),
    )

    end_time = time.time() + args.duration
    lock_events = 0
    loss_events = 0
    total_lines = 0
    sample_logs: list[str] = []

    try:
        while time.time() < end_time:
            if proc.poll() is not None:
                break

            if proc.stderr is None:
                time.sleep(0.1)
                continue

            line = proc.stderr.readline()
            if not line:
                time.sleep(0.1)
                continue

            line = line.strip()
            if not line:
                continue

            total_lines += 1
            if len(sample_logs) < 12:
                sample_logs.append(line)
            print(line)

            if is_lock_line(line):
                lock_events += 1
            if is_loss_line(line):
                loss_events += 1
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    rc = proc.poll()
    score = score_result(lock_events, loss_events, total_lines)
    return TestResult(
        frequency_mhz=args.frequency,
        hd_program=args.program,
        gain=gain,
        ppm=ppm,
        duration_sec=args.duration,
        return_code=rc,
        lock_events=lock_events,
        loss_events=loss_events,
        total_log_lines=total_lines,
        score=score,
        sample_logs=sample_logs,
        timestamp=time.time(),
    )


def load_results() -> list[dict]:
    if RESULTS_FILE.exists():
        try:
            return json.loads(RESULTS_FILE.read_text(encoding='utf-8'))
        except Exception:
            return []
    return []


def save_results(results: list[TestResult], append: bool) -> None:
    existing = load_results() if append else []
    data = existing + [asdict(result) for result in results]
    RESULTS_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')


def print_summary(results: list[TestResult]) -> None:
    ordered = sorted(results, key=lambda item: item.score, reverse=True)
    print('\n' + '#' * 78)
    print('Automatic tuning summary')
    print('#' * 78)
    for index, result in enumerate(ordered, start=1):
        print(
            f"{index:>2}. gain={result.gain:.1f} ppm={result.ppm:<4} "
            f"score={result.score:<4} locks={result.lock_events:<3} losses={result.loss_events:<3} "
            f"lines={result.total_log_lines:<4} rc={result.return_code}"
        )
    if ordered:
        best = ordered[0]
        print('\nBest candidate:')
        print(
            f"gain={best.gain:.1f}, ppm={best.ppm}, score={best.score}, "
            f"locks={best.lock_events}, losses={best.loss_events}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description='Automatic HD Radio gain/PPM tuning helper for SDR-BoomBox')
    parser.add_argument('--frequency', type=float, default=103.7, help='Station frequency in MHz')
    parser.add_argument('--program', type=int, default=0, help='HD subchannel number: 0=HD1, 1=HD2, etc.')
    parser.add_argument('--gains', default='14.4,19.7,28.0,36.4', help='Comma-separated gain candidates')
    parser.add_argument('--ppms', default='0', help='Comma-separated PPM candidates')
    parser.add_argument('--duration', type=int, default=20, help='Seconds to run each test')
    parser.add_argument('--device-index', type=int, default=0, help='RTL-SDR device index')
    parser.add_argument('--use-rtltcp', action='store_true', help='Use rtl_tcp instead of direct USB')
    parser.add_argument('--rtltcp-host', default='127.0.0.1', help='rtl_tcp host when --use-rtltcp is set')
    parser.add_argument('--append', action='store_true', help='Append results to hd_tuning_results.json instead of overwriting')
    args = parser.parse_args()

    ensure_tools()
    gains = parse_float_list(args.gains)
    ppms = parse_int_list(args.ppms)
    combos = list(itertools.product(gains, ppms))

    print(f'Running {len(combos)} automatic tests at {args.frequency:.1f} MHz HD{args.program + 1}.')
    print(f'Each test runs for {args.duration} seconds.')
    print(f'Results file: {RESULTS_FILE}')

    results: list[TestResult] = []
    for gain, ppm in combos:
        results.append(run_single_test(args, gain, ppm))

    save_results(results, append=args.append)
    print_summary(results)


if __name__ == '__main__':
    main()
