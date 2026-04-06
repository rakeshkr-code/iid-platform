Goal: **extract well, store well, link well, then retrieve well**.

---

# Full architecture (rough) diagram 

```text
                ┌──────────────────────────────────────────────────────┐
                │                    RAW SOURCES                       │
                │  PDFs | scanned PDFs | images | webpages | DOCX      │
                │  PPTX | TXT | JSON | CSV | APIs | email attachments   │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │               SOURCE ACQUISITION LAYER               │
                │  - crawler / downloader / file watcher               │
                │  - source registration                               │
                │  - checksum / dedup / version detection              │
                │  - raw immutable archive                             │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │             SOURCE CLASSIFICATION LAYER              │
                │  - file type detection                               │
                │  - scanned vs digital                                │
                │  - language detection                                │
                │  - modality detection                                │
                │  - complexity detection                              │
                │  - route to correct extractor path                   │
                └──────────────────────────────────────────────────────┘
                                      │
          ┌───────────────────────────┼───────────────────────────┐
          │                           │                           │
          ▼                           ▼                           ▼
┌───────────────────┐      ┌───────────────────┐      ┌───────────────────┐
│ PDF / DOCX path   │      │ Webpage path      │      │ Image / OCR path   │
│ - layout parsing  │      │ - HTML cleanup    │      │ - OCR             │
│ - table extract   │      │ - boilerplate     │      │ - vision caption   │
│ - hierarchy       │      │ - readability     │      │ - region extract   │
└───────────────────┘      └───────────────────┘      └───────────────────┘
          │                           │                           │
          └───────────────┬───────────┴───────────┬───────────────┘
                          ▼                       ▼
                ┌──────────────────────────────────────────────────────┐
                │           EXTRACTION / NORMALIZATION LAYER          │
                │  - text blocks                                       │
                │  - headings / sections                                │
                │  - paragraphs                                         │
                │  - tables / rows / cells                              │
                │  - figures / captions                                 │
                │  - OCR text blocks                                    │
                │  - page coordinates / bounding boxes                  │
                │  - confidence scores                                  │
                │  - provenance metadata                                │
                │  - canonical artifact IDs                             │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │            HUMAN REVIEW / APPROVAL GATE              │
                │  - table review                                       │
                │  - schema mapping                                     │
                │  - edit / approve / reject                           │
                │  - low-confidence escalation                          │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │               CANONICAL DATA MODEL                   │
                │  Every artifact becomes linkable and versioned:      │
                │  document -> pages -> sections -> blocks -> items    │
                │  (table, image, paragraph, entity, caption, etc.)    │
                └──────────────────────────────────────────────────────┘
                                      │
            ┌─────────────────────────┼─────────────────────────┐
            │                         │                         │
            ▼                         ▼                         ▼
┌────────────────────┐     ┌────────────────────┐     ┌────────────────────┐
│ OBJECT / BINARY    │     │ RELATIONAL STORE   │     │ FLEXIBLE DOCUMENT  │
│ STORE              │     │ (SQL)              │     │ STORE (NoSQL/JSON) │
│ - raw files        │     │ - metadata         │     │ - extracted payload │
│ - images           │     │ - approvals        │     │ - parser output     │
│ - OCR assets       │     │ - lineage          │     │ - nested structures │
└────────────────────┘     └────────────────────┘     └────────────────────┘
            │                         │                         │
            └─────────────────────────┼─────────────────────────┘
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │             LINK / HIERARCHY / GRAPH LAYER          │
                │  - section trees                                     │
                │  - parent-child relationships                        │
                │  - text-image-caption links                          │
                │  - table-to-page links                               │
                │  - entity and relation links                         │
                │  - version links                                     │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │                INDEXING LAYER                        │
                │  - vector embeddings                                 │
                │  - lexical index (BM25 / keyword)                    │
                │  - table index                                       │
                │  - image embeddings                                  │
                │  - metadata index                                    │
                │  - time/version-aware index                          │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │                RETRIEVAL LAYER                       │
                │  - query understanding                               │
                │  - routing to vector / SQL / image / API tools       │
                │  - metadata filtering                                │
                │  - hybrid retrieval                                  │
                │  - reranking                                         │
                │  - multi-hop / follow-up retrieval                   │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │                CONTEXT ASSEMBLY                      │
                │  - evidence selection                                │
                │  - prompt construction                               │
                │  - citations                                         │
                │  - recency / permission checks                       │
                │  - answer format constraints                         │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │                    LLM LAYER                         │
                │  - answer generation                                 │
                │  - structured output                                 │
                │  - tool calls / function calls                      │
                │  - clarification if evidence is insufficient         │
                └──────────────────────────────────────────────────────┘
                                      │
                                      ▼
                ┌──────────────────────────────────────────────────────┐
                │                FEEDBACK / OBSERVABILITY              │
                │  - logs                                              │
                │  - evaluation                                        │
                │  - human correction loop                             │
                │  - failed extraction handling                        │
                │  - reprocessing / reindexing                         │
                └──────────────────────────────────────────────────────┘
```
