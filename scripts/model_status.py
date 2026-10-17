"""python scripts/model_status.py [--warmup]; never downloads models."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai.diagnostics import inspect_models


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warmup', action='store_true', help='Preload configured models before checking GPU residency')
    args = parser.parse_args()
    try:
        print(json.dumps(inspect_models(warmup=args.warmup), indent=2))
    except Exception as error:
        print(f'Ollama diagnostics failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
