"""The knowledge library: typologies, policies and procedures that answers and agents cite.

Documents are Markdown with a small header (title, kind, scenarios, source), one file per document. The platform's
library lives in backend/knowledge/library; a client's own documents in backend/knowledge/clients/<client id>, which
only that client's people search and which replace a library document of the same name. Each section (a `##`
heading) is one passage, cited as `kb:<document>#<section>`.

Search is BM25 over words, in-process and dependency-free: the library is small, changes by pull request, and must
give the same answer every time. A deployment with a large corpus can put an index behind the same `search()`.
"""
from .corpus import Passage, documents, link, passage
from .search import search

__all__ = ["Passage", "documents", "link", "passage", "search"]
