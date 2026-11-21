# The knowledge library

Ask, the agents and MCP clients cite typologies, policies and procedures from one library. A cited passage is
checked and linked, in the same way as a cited record.

## Documents

One Markdown file per document, with a small YAML header. Each `##` section is one passage, cited as
`kb:<document>#<section>`.

```markdown
---
title: Structuring (smurfing)
kind: typology          # typology, policy, procedure or regulation
scenarios: [structuring] # alert scenarios it is about (optional)
source: Where it comes from
---
## Indicators
- Several cash deposits between $8,000 and $9,999 ...
```

- `backend/knowledge/library/`: the platform's library. Everyone at every client reads it. The eight documents
  shipped are sample content, to be replaced with the client's own.
- `backend/knowledge/clients/<client id>/`: a client's own documents. Only that client's people search, cite or read
  them. A file named like a library document replaces that document, for that client only.
- `KNOWLEDGE_DIR` points at another folder with the same layout. The library changes by pull request, like code.

A malformed document (bad name, no header, no title or kind, no sections) stops the app from loading the library,
with the reason.

## Search

BM25 over words, in process, with no dependencies. It gives the same answer every time. Light stemming and a short
list of synonyms map the way people ask ("signs", "red flags", "tipping off") to the words the library uses
("indicators", "tell"). A deployment with a large corpus can put a vector or hybrid index behind the same
`knowledge.search()`.

## Where it is used

- **Ask:** the `policy_search` tool. A model is told to cite the passage ids it relies on. Each cited id becomes a
  source showing the passage text, and an id that is not in this client's library is flagged as unverified. Without a
  model, the offline planner answers "what are the signs of...", "what does policy say...", "when must we file..." and
  similar questions from the library.
- **MCP:** the `policy_search` tool (see [mcp.md](mcp.md)).
- **API:**
  - `GET /api/knowledge` lists documents;
  - `GET /api/knowledge/search?q=` searches;
  - `GET /api/knowledge/{document}` reads one document.
- **App:** the Knowledge library page (`/knowledge`).
