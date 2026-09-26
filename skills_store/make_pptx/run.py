import argparse
import json
import os
import sys
from pathlib import Path

from pptx import Presentation
from pptx.util import Pt
from pptx.oxml.ns import qn


def resolve_output_path(raw_path: str) -> Path:
    """Real bug this fixes: skills/skill_loader.py runs this script with its
    OWN directory as the subprocess cwd (so a skill can reference its own
    local files by relative path) -- that is NOT the workspace
    tools/files/file_tool.py's tools operate on, so a plain
    `prs.save(raw_path)` either landed the .pptx somewhere the rest of the
    app could never find it, or (for any raw_path with a subdirectory)
    crashed outright, since python-pptx does not create missing parent
    directories itself.

    skills/skill_loader.py injects AURA_WORKSPACE_ROOT into this process's
    environment when the caller configured one (core/bootstrap.py always
    does) -- mirrors tools/sandbox_path.py::resolve_within_sandbox()'s
    exact security checks (reject an absolute path before ever joining it,
    since Path(root) / "/etc/passwd" would silently discard `root`
    entirely; reject anything that resolves outside the root after the
    join) since a skill's run.py is a standalone script and must not import
    back into the main package. With no AURA_WORKSPACE_ROOT set (e.g. this
    script run standalone, or via a SkillLoader that didn't configure one),
    falls back to the pre-fix behavior: `raw_path` as-is, relative to cwd.
    """
    workspace_root = os.environ.get("AURA_WORKSPACE_ROOT")
    if not workspace_root:
        return Path(raw_path)

    root = Path(workspace_root).resolve()
    if Path(raw_path).is_absolute():
        print(f"Absolute paths are not allowed: '{raw_path}'", file=sys.stderr)
        sys.exit(1)
    candidate = (root / raw_path).resolve()
    if not candidate.is_relative_to(root):
        print(f"Path '{raw_path}' resolves outside the workspace sandbox ({root})", file=sys.stderr)
        sys.exit(1)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    return candidate


def set_cjk(run, size, font, bold=False):
    """Set font size/bold and apply the typeface to latin, East Asian and
    complex-script runs, so Chinese glyphs do not fall back unpredictably."""
    run.font.size = Pt(size)
    run.font.bold = bold
    rPr = run.font._rPr
    for tag in ("a:latin", "a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", font)


def add_slide(prs, slot, title, bullets, font, title_size, body_size):
    slide = prs.slides.add_slide(prs.slide_layouts[slot])
    slide.shapes.title.text = title
    for run in slide.shapes.title.text_frame.paragraphs[0].runs:
        set_cjk(run, title_size, font, bold=True)
    body = slide.placeholders[1].text_frame
    for i, bullet in enumerate(bullets):
        para = body.paragraphs[0] if i == 0 else body.add_paragraph()
        para.text = bullet
        for run in para.runs:
            set_cjk(run, body_size, font)
    return slide


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--args-json", required=True)
    args = json.loads(parser.parse_args().args_json)

    font = args.get("font", "微软雅黑")
    prs = Presentation()

    # Title slide (layout 0), then content slides (layout 1).
    title_slide = prs.slides.add_slide(prs.slide_layouts[0])
    title_slide.shapes.title.text = args["title"]
    for run in title_slide.shapes.title.text_frame.paragraphs[0].runs:
        set_cjk(run, 40, font, bold=True)

    for item in args["slides"]:
        add_slide(prs, 1, item["title"], item["bullets"], font, 30, 20)

    output_path = resolve_output_path(args["output_path"])
    prs.save(str(output_path))
    print(f"已生成 {args['output_path']}（共 {len(prs.slides._sldIdLst)} 页）")


if __name__ == "__main__":
    main()
