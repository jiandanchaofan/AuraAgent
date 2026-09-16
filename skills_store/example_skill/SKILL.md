---
name: word_count
description: Counts words in a block of text. Example skill proving the SkillLoader discovery path.
input_schema:
  type: object
  properties:
    text:
      type: string
      description: The text to count words in.
  required:
    - text
---

# word_count

Counts the number of whitespace-separated words in the given text.

Invocation: `python run.py --args-json '{"text": "..."}'` — every skill's
`run.py` receives its arguments as a single JSON blob via `--args-json`
(see skills/skill_loader.py).
