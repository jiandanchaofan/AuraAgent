"""Example skill entrypoint: prints the word count of the text passed via --text.

Not yet wired up by SkillLoader (see skills/skill_loader.py) — this file
exists so the loader has a real target to discover once implemented.
"""
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    args = parser.parse_args()
    print(len(args.text.split()))
