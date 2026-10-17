"""Router-only evaluation; never captures screens or executes desktop commands."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from router.router import MoteRouter

# Expectations are independent of model output. Include paraphrases that require inference.
CASES = [
    ('Open Visual Studio Code', ['open_application'], False),
    ('Open Thunderbird', ['open_application'], False),
    ('Open example.org/docs?q=test', ['open_website'], False),
    ('open youtube and set the volume to 50', ['open_website', 'set_volume'], False),
    ('Please bring up Firefox for me', ['open_application'], False),
    ('Could you launch YouTube, then put system audio at 50 percent?', ['open_website', 'set_volume'], False),
    ('Silence the audio', ['adjust_volume'], False),
    ('What is on my screen?', None, True),
    ('Diagnose this visible error', None, True),
    ('What does this mean?', None, True),
    ('Where should I click?', None, True),
    ('Explain this error: division by zero', None, False),
    ('What can you do?', None, False),
    ('How do I open Notepad?', None, False),
    ("Don't open Notepad", None, False),
    ('Open YouTube and set volume to', None, False),
    ('Open Notepad if the download finishes', None, False),
    ('Open YouTube and delete every file', None, False),
    ('What is two plus two?', None, False),
]


def evaluate(router, cases=CASES):
    rows = []
    for query, expected_commands, screenshot in cases:
        start = time.perf_counter()
        try:
            decision = router.route(query)
            commands = decision['command']
            commands = commands if isinstance(commands, list) else ([commands] if commands else [])
            actual = [command['name'] for command in commands] or None
            passed = actual == expected_commands and decision['screenshot'] == screenshot
            # Check the motivating request's arguments as well as command names/order.
            if query == 'open youtube and set the volume to 50' and passed:
                host = urlparse(commands[0]['arguments']['url']).hostname or ''
                passed = commands[1]['arguments'] == {'level_percent': 50} and host in {'youtube.com', 'www.youtube.com'}
            row = {'query': query, 'passed': passed, 'decision': decision}
        except Exception as error:
            row = {'query': query, 'passed': False, 'error': str(error)}
        row['milliseconds'] = round((time.perf_counter() - start) * 1000, 2)
        rows.append(row)
        print(f"{'PASS' if row['passed'] else 'FAIL'} {row['milliseconds']:9.2f} ms  {query}", flush=True)
        if not row['passed']:
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-only', action='store_true', help='Disable fast paths to measure classifier accuracy and model latency')
    parser.add_argument('--output', type=Path, help='Save full decisions and timings as JSON')
    args = parser.parse_args()
    router = MoteRouter(fast_paths=not args.model_only)
    rows = evaluate(router)
    report = {'model': router.model, 'fast_paths': router.fast_paths, 'passed': sum(row['passed'] for row in rows),
              'total': len(rows), 'median_ms': statistics.median(row['milliseconds'] for row in rows), 'cases': rows}
    print(f"{report['passed']}/{report['total']} passed; median {report['median_ms']:.2f} ms")
    if args.output:
        args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    sys.exit(main())
