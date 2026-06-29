"""业务串联层."""

from app.services.document_loader import load_and_split, read_text_file
from app.services.html_cleaner import extract_main_content, html_to_markdown
from app.services.retriever import Retriever, retriever
from app.services.text_splitter import (
    split_generic,
    split_markdown_spec,
    split_review_case,
)
from app.services.url_fetcher import FetchedDoc, fetch_url

__all__ = [
    "Retriever",
    "retriever",
    "load_and_split",
    "read_text_file",
    "extract_main_content",
    "html_to_markdown",
    "split_markdown_spec",
    "split_review_case",
    "split_generic",
    "FetchedDoc",
    "fetch_url",
]
