# Queue Processing & Persistence Overview

This document summarizes how jobs are created, processed, and stored across Redis and SQLite. File paths below point to the authoritative implementation for each step.

## What is a job?
- **Definition:** A job represents one attempt to apply (or re-apply) an AI title to a single Paperless document.
- **Data model:** `ProcessingJob` in `src/paperless_ai_titles/core/models.py` stores the job row. Each row is tied to a `document_id` and includes status, timestamps, last error, reason text, and the raw LLM response payload once processing completes.
- **Job types:** There is a single job type (`process_document`). Every enqueue operation schedules this handler via RQ (`src/paperless_ai_titles/rq_task_handlers.py`). Variants such as scanner, hook, manual enqueue, or force reprocess only change the `source` and `reason` metadata.

## Job fields
| Field | Purpose | Set/Updated In |
| --- | --- | --- |
| `id` | Primary key used as the RQ argument. | `services/jobs.py::enqueue_document`
| `document_id` | Paperless document that will be processed. | `enqueue_document`
| `status` | Lifecycle marker (`queued`, `running`, `completed`, `awaiting_approval`, `skipped`, `failed`, `rejected`). | `enqueue_document`, `processing._set_job_running`, `_apply_plan`, `_store_pending_plan`, `_mark_skipped`, `mark_failure`, `deny_pending`
| `source` | Origin of the enqueue (scanner, hook, api, manual, force-reprocess). | `enqueue_document`
| `reason` | Human-readable explanation (eligibility reason, skip note, manual denial, etc.). | `enqueue_document`, `_store_pending_plan`, `_mark_skipped`, `mark_failure`, `deny_pending`
| `attempt_count` | Incremented each time a worker picks up the job. | `processing._set_job_running`
| `last_error` | Captures exception text for debugging. | `mark_failure`
| `llm_response` | Raw JSON returned from the LLM for auditing. | `_apply_plan`, `_store_pending_plan`
| `queued_at`, `completed_at`, `created_at`, `updated_at` | Timing metadata for dashboards and retry analysis. | Auto-set by SQLAlchemy plus explicit assignments in `enqueue_document`, `_apply_plan`, `_store_pending_plan`, `_mark_skipped`, `mark_failure`

`DocumentRecord` (same file) mirrors per-document status, original/AI titles, confidence, lock reasons, and `extra` metadata. These rows allow the app to know which documents have already been processed, regardless of what remains in Redis.

## Lifecycle & Filtering
1. **Candidate discovery (`services/scanner.py`):** The scanner now enqueues every Paperless document it sees (up to its per-run cap) unless the document already has a `DocumentRecord` in a finalized state (`completed`, `skipped`, or `rejected`). It no longer performs tag/original-title filtering; that responsibility is centralized in the worker. Manual/API/Hook requeues still flow through `enqueue_document`, so every job enters the queue the same way.
2. **Job creation (`services/jobs.py`):** Deduplicates against active jobs, writes/updates `ProcessingJob` + `DocumentRecord`, and enqueues `process_document` on the Redis-backed RQ queue with retry + timeout values derived from settings.
3. **Worker execution (`src/paperless_ai_titles/rq_worker_runner.py` + `rq_task_handlers.py`):** The RQ worker watches the queue and, once onboarding is complete, executes `process_document(job_id, document_id)` for each dequeued task, calling `services/processing.run_processing_job`.
4. **Plan building (`services/processing.py`):** Fetches the document (via `clients/paperless_client.py`), applies the tag/original-title eligibility rules (`document_eligibility.py`), evaluates existing titles via the LLM when present, and decides whether a new title is required. The plan captures reason text, the new suggestion, OCR context, and evaluation metadata.
5. **Outcome persistence (`services/processing.py`):**
   - `_apply_plan` writes the AI title to Paperless, records the original title in a custom field, and marks both `ProcessingJob` and `DocumentRecord` as `completed` with confidence + metadata snapshots.
   - `_store_pending_plan` is used when auto-apply is disabled or confidence falls below the threshold; it stores a pending payload under `DocumentRecord.extra`, updates statuses to `awaiting_approval`, and saves the LLM payload for reviewers.
   - `_mark_skipped` handles cases where the plan determined no change was necessary (e.g., LLM approved existing title). `mark_failure` and `deny_pending` record errors or reviewer decisions respectively.
6. **Manual approvals (`routers/api.py` + `ProcessingService.approve_pending/deny_pending`):** Approvals fetch the pending snapshot, re-fetch the document for context, and call `_apply_plan` to finish the job; denials mark the job/document as `rejected` with the reviewer-provided reason.

Throughout this flow, every decision logs at DEBUG level (see `services/processing.py`, `services/scanner.py`, `document_eligibility.py`) so you can trace why a job moved to a given state.

## Redis vs. SQLite responsibilities
| Storage | What lives there | Source Files |
| --- | --- | --- |
| **Redis** | RQ queue contents (pending jobs, scheduler metadata, retry state) and transient worker bookkeeping. Clearing Redis removes in-flight tasks, but completed history lives elsewhere. Persistence depends on your deployment (the default `docker-compose.yml` mounts a `redis_data` volume so queue state survives container restarts). | `queue_factory.py`, `docker-compose*.yml`
| **SQLite** | All durable application data: `ProcessingJob` rows, `DocumentRecord` rows, runtime `Setting` overrides, onboarding flags, plus any job/document metadata used by the dashboard, approvals, and reprocess logic. This is the source of truth for what has been processed, when, and with which outcomes. | `core/models.py`, `core/database.py`, writers in `services/jobs.py`, `services/processing.py`, `services/settings.py`

Because SQLite tracks both job status and per-document outcomes, the service can safely determine whether a document needs work even if Redis is flushed or a worker restarts. Redis only needs to hold the active work queue.
