from __future__ import annotations

from uuid import uuid4

from app.ai.chat_engine import ChatEngine
from app.core.settings import get_settings
from app.rag.types import RagHit


def test_citations_include_page_range_and_pptx_fields() -> None:
    engine = ChatEngine(get_settings())

    cid = str(uuid4())
    hits = [
        RagHit(
            text="Eigenvalues are ...",
            metadata={
                "content_id": cid,
                "doc_type": "pdf",
                "original_filename": "midterm.pdf",
                "page_start": 3,
                "page_end": 4,
                "source_kind": "pdf",
            },
            score=0.1,
        ),
        RagHit(
            text="Slide content ...",
            metadata={
                "content_id": cid,
                "doc_type": "slides",
                "original_filename": "lecture.pptx",
                "page_start": 5,
                "page_end": 5,
                "source_kind": "pptx",
                "slide_no": 5,
            },
            score=0.2,
        ),
    ]

    citations = engine._hits_to_citations(hits)  # noqa: SLF001 (test private helper)
    assert len(citations) == 2

    c0 = citations[0]
    assert c0.extra is not None
    assert c0.extra.get("pageStart") == 3
    assert c0.extra.get("pageEnd") == 4
    assert c0.extra.get("sourceKind") == "pdf"

    c1 = citations[1]
    assert c1.extra is not None
    assert c1.extra.get("pageStart") == 5
    assert c1.extra.get("pageEnd") == 5
    assert c1.extra.get("sourceKind") == "pptx"
    assert c1.extra.get("slideNo") == 5


