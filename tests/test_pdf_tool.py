"""Tests for tools/documents/pdf_tool.py -- read_pdf. PDF has no
create/edit counterpart (see that module's docstring for why), so unlike
the docx/pptx/xlsx test files this can't do a create-then-read round
trip; instead it hand-builds a minimal valid PDF (a handful of objects
plus a correct xref table) with one page of known text, the same
'./verified this parses with pypdf' fixture called for by the approved
plan.
"""
from __future__ import annotations

import pytest

from core.exceptions import ToolExecutionError
from tools.documents.pdf_tool import register_pdf_tools
from tools.registry import ToolRegistry
from tools.workspace_root import SwappableWorkspaceRoot


def _minimal_pdf_bytes(*texts: str) -> bytes:
    """Builds a minimal valid single- or multi-page PDF, one text line per
    page, via BT/Tf/Td/Tj/ET content streams -- no external PDF-writing
    library involved."""
    objects: list[bytes] = []
    page_count = len(texts)

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + i} 0 R" for i in range(page_count))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>".encode())

    font_obj_num = 3 + page_count
    for i in range(page_count):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 {font_obj_num} 0 R >> >> "
                f"/MediaBox [0 0 200 200] /Contents {font_obj_num + 1 + i} 0 R >>"
            ).encode()
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for text in texts:
        content = f"BT /F1 24 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")

    header = b"%PDF-1.4\n"
    body = bytearray()
    offsets = [0]
    pos = len(header)
    for i, obj in enumerate(objects, start=1):
        offsets.append(pos)
        obj_bytes = f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
        body += obj_bytes
        pos += len(obj_bytes)

    xref_offset = pos
    xref_lines = [b"xref", f"0 {len(objects) + 1}".encode(), b"0000000000 65535 f "]
    for off in offsets[1:]:
        xref_lines.append(f"{off:010d} 00000 n ".encode())
    xref_bytes = b"\n".join(xref_lines) + b"\n"
    trailer = f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()

    return header + bytes(body) + xref_bytes + trailer


def _registry(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    register_pdf_tools(registry, SwappableWorkspaceRoot(workspace), max_read_bytes=20_000_000, max_extract_chars=50_000)
    return registry, workspace


@pytest.mark.asyncio
async def test_read_pdf_extracts_text_from_a_real_pdf(tmp_path):
    registry, workspace = _registry(tmp_path)
    (workspace / "doc.pdf").write_bytes(_minimal_pdf_bytes("Hello World"))

    result = await registry.dispatch("read_pdf", {"path": "doc.pdf"})

    assert "Hello World" in result
    assert "--- Page 1 ---" in result


@pytest.mark.asyncio
async def test_read_pdf_with_page_range(tmp_path):
    registry, workspace = _registry(tmp_path)
    (workspace / "doc.pdf").write_bytes(_minimal_pdf_bytes("Page One Text", "Page Two Text", "Page Three Text"))

    result = await registry.dispatch("read_pdf", {"path": "doc.pdf", "pages": "2"})

    assert "Page Two Text" in result
    assert "Page One Text" not in result
    assert "Page Three Text" not in result


@pytest.mark.asyncio
async def test_read_pdf_with_page_range_span(tmp_path):
    registry, workspace = _registry(tmp_path)
    (workspace / "doc.pdf").write_bytes(_minimal_pdf_bytes("Page One Text", "Page Two Text", "Page Three Text"))

    result = await registry.dispatch("read_pdf", {"path": "doc.pdf", "pages": "1-2"})

    assert "Page One Text" in result
    assert "Page Two Text" in result
    assert "Page Three Text" not in result


@pytest.mark.asyncio
async def test_read_pdf_rejects_invalid_page_range(tmp_path):
    registry, workspace = _registry(tmp_path)
    (workspace / "doc.pdf").write_bytes(_minimal_pdf_bytes("Hello World"))

    with pytest.raises(ToolExecutionError, match="Invalid page range"):
        await registry.dispatch("read_pdf", {"path": "doc.pdf", "pages": "not-a-range"})


@pytest.mark.asyncio
async def test_read_pdf_rejects_out_of_range_page(tmp_path):
    registry, workspace = _registry(tmp_path)
    (workspace / "doc.pdf").write_bytes(_minimal_pdf_bytes("Hello World"))

    with pytest.raises(ToolExecutionError, match="beyond the document's"):
        await registry.dispatch("read_pdf", {"path": "doc.pdf", "pages": "5"})


@pytest.mark.asyncio
async def test_read_pdf_missing_file(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="File not found"):
        await registry.dispatch("read_pdf", {"path": "missing.pdf"})


@pytest.mark.asyncio
async def test_read_pdf_rejects_oversized_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    register_pdf_tools(registry, SwappableWorkspaceRoot(workspace), max_read_bytes=10, max_extract_chars=50_000)
    (workspace / "big.pdf").write_bytes(_minimal_pdf_bytes("Hello World"))

    with pytest.raises(ToolExecutionError, match="over the"):
        await registry.dispatch("read_pdf", {"path": "big.pdf"})


@pytest.mark.asyncio
async def test_read_pdf_truncates_long_output(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    registry = ToolRegistry()
    register_pdf_tools(registry, SwappableWorkspaceRoot(workspace), max_read_bytes=20_000_000, max_extract_chars=20)
    (workspace / "doc.pdf").write_bytes(_minimal_pdf_bytes("Hello World"))

    result = await registry.dispatch("read_pdf", {"path": "doc.pdf"})

    assert "truncated" in result


@pytest.mark.asyncio
async def test_read_pdf_blocks_path_traversal(tmp_path):
    registry, _ = _registry(tmp_path)

    with pytest.raises(ToolExecutionError, match="outside the sandbox root"):
        await registry.dispatch("read_pdf", {"path": "../escaped.pdf"})


@pytest.mark.asyncio
async def test_read_pdf_rejects_a_non_pdf_file(tmp_path):
    registry, workspace = _registry(tmp_path)
    (workspace / "not_a_pdf.pdf").write_text("this is not a real pdf", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="Could not open"):
        await registry.dispatch("read_pdf", {"path": "not_a_pdf.pdf"})
