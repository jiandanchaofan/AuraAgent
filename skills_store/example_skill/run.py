"""Example skill entrypoint: counts words in the "text" argument.

Follows the invocation convention every skill's run.py must follow — all
arguments arrive as a single JSON blob via --args-json, not individual CLI
flags (see skills/skill_loader.py for why).
"""
import argparse
import json

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-json", required=True)
    args = parser.parse_args()
    payload = json.loads(args.args_json)
    print(len(payload["text"].split()))
