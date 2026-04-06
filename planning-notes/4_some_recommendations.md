# Relationship model should be followed

The system should obey this chain:

```text
source
  -> source_version
    -> raw_asset
      -> document_version
        -> page
        -> sections
        -> blocks / tables / images / captions
        -> links
        -> chunks
        -> embeddings / search index
        -> retrieval results
        -> LLM answer
```

This chain is the backbone of traceability.

---

# Recommended MVP scope

To keep the first version sane, it is recommended to scope the MVP like this:

## Input types

* digital PDFs
* scanned PDFs
* webpages

## Extraction outputs

* text
* headings
* tables
* images
* captions
* page metadata

## Storage

* raw file archive
* relational metadata DB
* object store
* JSON extraction store
* vector store

## Human review

* tables only, at first

## Retrieval

* vector search
* metadata filter
* basic SQL lookup
* answer with citations

This is enough to prove the architecture.

---

# What to postpone until later

Do not start with these:

* full graph DB
* multi-agent orchestration
* complex entity resolution
* automatic schema learning for every table
* too many source types
* too many index types
* perfect multimodal fusion

Those are all valid future additions, but they will slow down the progress early.

---

# The right implementation principle

The first system should optimize for:

**correct extraction → stable schema → traceable storage → reliable retrieval**

Not for:

* fancy prompting
* many agent tools
* many models
* maximum automation too early

---

# The architecture in one compact view

```text
Raw source
  -> classify
  -> extract
  -> normalize
  -> human review where needed
  -> store canonical artifacts
  -> link artifacts
  -> index artifacts
  -> retrieve by query type
  -> rerank
  -> assemble context
  -> answer with citations
  -> log feedback
  -> reprocess when source changes
```
