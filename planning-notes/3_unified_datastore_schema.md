# Database / schema design for the unified datastore

**logical schema** design with a practical physical mapping:

## Recommended physical storage map

### 1. Object store

For:

* raw source files
* page images
* extracted images
* OCR artifacts
* previews

### 2. Relational DB

For:

* documents
* versions
* metadata
* approvals
* lineage
* relations
* permissions
* processing runs

### 3. NoSQL / JSON store

For:

* parser output
* nested extraction payloads
* flexible document structures
* irregular metadata

### 4. Search index

For:

* lexical search
* hybrid retrieval

### 5. Vector store

For:

* text embeddings
* image embeddings
* chunk embeddings
* table embeddings

### 6. Optional graph store

For:

* rich links
* hierarchy
* cross-document relations

Every component is not needeed on day one, but this is the clean target architecture.

---

## Core logical entities

### A. Source-level entities

#### `sources`

Represents the original source location or origin.

Fields:

* `source_id`
* `source_type`
  (pdf, webpage, docx, image, api, etc.)
* `source_uri`
* `source_name`
* `source_owner`
* `ingestion_policy`
* `created_at`

#### `source_versions`

Represents one ingested snapshot of a source.

Fields:

* `source_version_id`
* `source_id`
* `checksum`
* `version_label`
* `captured_at`
* `ingested_at`
* `content_hash`
* `status`

Purpose:

* version tracking
* dedup
* incremental ingestion

---

### B. Raw artifact entities

#### `raw_assets`

Represents immutable raw files.

Fields:

* `raw_asset_id`
* `source_version_id`
* `storage_uri`
* `mime_type`
* `size_bytes`
* `page_count`
* `width`
* `height`
* `language_hint`
* `ocr_required`
* `created_at`

---

### C. Document-level entities

#### `documents`

Represents the logical document.

Fields:

* `document_id`
* `source_id`
* `current_version_id`
* `title`
* `doc_type`
* `domain`
* `language`
* `created_at`
* `updated_at`

#### `document_versions`

Represents one parsed or revised version.

Fields:

* `document_version_id`
* `document_id`
* `source_version_id`
* `parser_name`
* `parser_version`
* `extraction_status`
* `review_status`
* `confidence_score`
* `created_at`

---

### D. Hierarchical structure entities

#### `pages`

Fields:

* `page_id`
* `document_version_id`
* `page_number`
* `storage_uri_image`
* `width`
* `height`
* `rotation`
* `created_at`

#### `sections`

Fields:

* `section_id`
* `document_version_id`
* `parent_section_id`
* `heading`
* `section_level`
* `start_page`
* `end_page`
* `section_path`
* `order_index`

Purpose:

* hierarchical text structure

#### `blocks`

Generic block table for paragraphs, list items, headings, OCR blocks, etc.

Fields:

* `block_id`
* `page_id`
* `section_id`
* `block_type`
* `text`
* `bbox`
* `reading_order`
* `confidence_score`
* `source_span_start`
* `source_span_end`

This table is very useful as a canonical bridge between raw extraction and higher-level entities.

---

### E. Table entities

#### `tables`

Fields:

* `table_id`
* `document_version_id`
* `page_id`
* `section_id`
* `caption`
* `table_type`
* `structure_confidence`
* `review_status`
* `schema_name`
* `created_at`

#### `table_rows`

Fields:

* `row_id`
* `table_id`
* `row_index`
* `is_header`
* `is_total_row`

#### `table_cells`

Fields:

* `cell_id`
* `row_id`
* `col_index`
* `text`
* `normalized_value`
* `bbox`
* `rowspan`
* `colspan`
* `confidence_score`

#### `table_schemas`

Fields:

* `schema_id`
* `schema_name`
* `schema_definition_json`
* `version`
* `approved_by`
* `created_at`

This is where human approval gate fits naturally.

---

### F. Figure and image entities

#### `images`

Fields:

* `image_id`
* `document_version_id`
* `page_id`
* `storage_uri`
* `bbox`
* `image_type`
* `caption_id`
* `ocr_text`
* `image_embedding_id`
* `created_at`

#### `captions`

Fields:

* `caption_id`
* `document_version_id`
* `text`
* `linked_object_type`
* `linked_object_id`
* `confidence_score`

This lets us link image ↔ caption ↔ surrounding text.

---

### G. Metadata and provenance

#### `artifact_metadata`

Generic metadata table.

Fields:

* `metadata_id`
* `artifact_type`
* `artifact_id`
* `key`
* `value`
* `value_type`
* `source`
* `created_at`

Examples:

* author
* department
* source page
* confidence
* language
* region
* effective date

#### `provenance`

Fields:

* `provenance_id`
* `artifact_type`
* `artifact_id`
* `source_version_id`
* `extractor_name`
* `extractor_version`
* `created_at`

Purpose:

* auditability
* reproducibility
* debugging

---

### H. Versioning and review

#### `approvals`

Fields:

* `approval_id`
* `artifact_type`
* `artifact_id`
* `reviewer_id`
* `decision`
* `comments`
* `edited_payload_json`
* `reviewed_at`

#### `edit_history`

Fields:

* `edit_id`
* `artifact_type`
* `artifact_id`
* `old_value_json`
* `new_value_json`
* `editor_id`
* `edited_at`

#### `processing_runs`

Fields:

* `run_id`
* `source_version_id`
* `pipeline_name`
* `pipeline_version`
* `status`
* `error_message`
* `started_at`
* `finished_at`

---

### I. Linking entities

#### `artifact_links`

This is one of the most important tables.

Fields:

* `link_id`
* `from_artifact_type`
* `from_artifact_id`
* `to_artifact_type`
* `to_artifact_id`
* `link_type`
* `confidence_score`
* `created_at`

Examples of `link_type`:

* contains
* refers_to
* caption_of
* extracted_from
* same_as
* version_of
* related_to
* supports_claim

This is how the system becomes truly unified.

---

### J. Search and retrieval entities

#### `text_chunks`

Fields:

* `chunk_id`
* `document_version_id`
* `artifact_type`
* `artifact_id`
* `chunk_text`
* `chunk_order`
* `token_count`
* `embedding_id`
* `created_at`

#### `embeddings`

Fields:

* `embedding_id`
* `artifact_type`
* `artifact_id`
* `embedding_model`
* `vector_uri_or_key`
* `dimensionality`
* `created_at`

#### `lexical_index_records`

Fields:

* `lexical_id`
* `artifact_type`
* `artifact_id`
* `terms`
* `index_key`
* `created_at`

#### `retrieval_queries`

Fields:

* `query_id`
* `user_id`
* `query_text`
* `query_type`
* `created_at`

#### `retrieval_results`

Fields:

* `result_id`
* `query_id`
* `artifact_type`
* `artifact_id`
* `score`
* `rank`
* `retriever_name`
* `created_at`

---

### K. Access control

#### `permissions`

Fields:

* `permission_id`
* `subject_type`
* `subject_id`
* `resource_type`
* `resource_id`
* `action`
* `scope`
* `created_at`

Examples:

* user can read document
* role can query department data
* tenant can access only its sources

This is necessary if the system will be used in any realistic enterprise setting.
