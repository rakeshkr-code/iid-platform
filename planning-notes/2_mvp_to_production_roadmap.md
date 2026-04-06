# Step-by-step build roadmap from MVP to production

## Phase 0: Design the contract first

Before coding, define:

* source types
* artifact types
* metadata schema
* versioning policy
* review policy
* link policy
* access control model
* success metrics

Deliverables:

* canonical data model
* naming conventions
* ID strategy
* storage mapping
* review rules

This phase prevents the system from becoming a pile of disconnected scripts.

---

## Phase 1: MVP extraction pipeline

Goal: support a small set of difficult sources well.

Start with:

* digital PDFs
* scanned PDFs
* webpages

Build:

* raw file ingestion
* source fingerprinting
* basic file type detection
* extraction pipeline
* OCR fallback for scanned pages
* normalized output format
* raw archive storage
* one metadata store
* one document store

Output:

* clean extracted text
* page references
* source IDs
* timestamps
* confidence scores

Success criteria:

* extracted text is readable
* every extracted item maps back to source page
* the same file reprocessed twice gives stable IDs

---

## Phase 2: Structure-aware extraction

Goal: preserve document meaning, not just text.

Add:

* headings and hierarchy detection
* paragraph segmentation
* table extraction
* figure extraction
* caption linking
* page layout metadata
* document section tree

Add human review gate for:

* tables
* ambiguous blocks
* low-confidence OCR
* broken reading order

Success criteria:

* tables are usable
* text is in correct order
* sections are preserved
* human corrections are stored cleanly

---

## Phase 3: Unified datastore

Goal: separate raw, structured, and linkable content cleanly.

Add:

* SQL relational schema
* NoSQL flexible extraction store
* binary/image store
* hierarchy/link store
* version tables
* provenance tables
* approval tables

Add:

* raw → extracted → approved → canonical state machine
* reprocessing logic
* update handling for changed documents

Success criteria:

* same source can have multiple versions
* approved extraction becomes canonical
* old versions remain queryable

---

## Phase 4: Indexing layer

Goal: make the datastore searchable.

Add:

* text embeddings
* lexical index
* metadata index
* image embeddings
* table-aware indexing
* version-aware indexing

Start with:

* vector search
* keyword search
* metadata filters
* recency filters

Success criteria:

* user can search text and structured fields
* image and text can be linked
* old versions do not pollute active retrieval

---

## Phase 5: Retrieval orchestration

Goal: make query routing intelligent.

Add:

* query classification
* intent detection
* entity extraction
* routing to SQL or vector or image tools
* hybrid retrieval
* reranking
* follow-up retrieval
* multi-hop retrieval

Success criteria:

* the system can answer from text, tables, or linked images
* queries go to the right backend automatically
* retrieval quality improves with reranking

---

## Phase 6: RAG answering layer

Goal: generate grounded answers with traceability.

Add:

* context assembly
* prompt template
* citation formatting
* recency handling
* uncertainty handling
* answer schema
* follow-up question fallback

Success criteria:

* answers cite the right artifacts
* insufficient evidence triggers safe fallback
* answer format is consistent

---

## Phase 7: Production hardening

Goal: make it reliable and maintainable.

Add:

* observability
* evaluation dashboards
* dead-letter queue for failed documents
* retries and backoff
* caching
* permission filtering
* audit logs
* data retention policy
* schema evolution support

Success criteria:

* system can run continuously
* failures are visible and recoverable
* users can trust what changed and why

---

## Phase 8: Advanced capabilities

Only after the foundation is stable, add:

* multi-document reasoning
* tool calling
* API integration
* agentic workflows
* cross-source conflict resolution
* entity resolution across documents
* auto-annotation / weak supervision
