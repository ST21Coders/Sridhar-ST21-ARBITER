# ARBITER ST21 — Feature Coverage Plan

This document captures a workflow-driven audit of four feature claims against the current ARBITER ST21 build, the per-feature gap analysis, and the consolidated step-by-step backlog to close the gaps.

Companion documents:
- [`Documents/Dashboard_guide.md`](Dashboard_guide.md) — full system guide
- [`Documents/scan-flow-path.md`](scan-flow-path.md) — Run AI Scan internals
- [`Documents/arbiter_st21_dashboard_architecture.drawio`](arbiter_st21_dashboard_architecture.drawio) — target-state architecture

## Feature claims under audit

| Key | Claim |
|---|---|
| F1 | Detect policy conflicts as they happen — new docs uploaded to S3 / fed into KB auto-trigger detection |
| F2 | CR creation and approval workflow in motion (end-to-end) |
| F3 | Compliance reporting pulled live across platforms (PCI-DSS, NAIC, SOC 2, ISO 27001) |
| F4 | Analyst chat capability to answer about live conflicts/findings obtained via Run AI Scan |

## 1. Coverage matrix

| Feature | Status | Confidence | One-line summary |
|---|---|---|---|
| F1 — Detect conflicts as docs are uploaded | **SUPPORTED** | High | Auto-detect chain live (`s3:ObjectCreated → processing_pipeline → KB ingestion → scanner`). Measured end-to-end ~30s from upload to fresh `scan-runs` COMPLETED row. See [Dashboard_guide.md §14](Dashboard_guide.md#14-operational-notes). |
| F2 — CR creation & approval workflow | SUPPORTED | High | Full end-to-end: tiered approver chains, CISO override, execute resolves linked conflict, UI refreshes on every transition. |
| F3 — Live compliance reporting (PCI/NAIC/SOC2/ISO) | PARTIAL | High | Framework tagging on 6/12 UCs, Governance scorecard live, but CSV-only export, no PDF, no scheduled reports, SOC2/ISO27001 partially orphaned. |
| F4 — Analyst chat over live findings | PARTIAL | High | Scan pipeline real and persisted; chat has no tool to read `conflicts-v2`, so it can't ground answers in scan output. |

## 2. Per-feature deep dive

### F1 — Detect conflicts as they happen

**Current state**
- [`processing_pipeline.py:1-5`](../Infra/functions/processing_pipeline/processing_pipeline.py#L1) — invoked via EventBridge cron only (06:00 / 18:00 PST).
- [`05-compute.yaml:137-155`](../Infra/templates/05-compute.yaml#L137) — `ProcessingPipelineSchedule` is `cron(0 14,2 * * ? *)`, no S3 event.
- [`04-storage.yaml:24-68`](../Infra/templates/04-storage.yaml#L24) — `RawBucket` / `ProcessedBucket` have no `NotificationConfiguration`.
- [`11-scanner.yaml:152-172`](../Infra/templates/11-scanner.yaml#L152) — scanner runs on `cron(0 6 * * ? *)` only.
- [`Dashboard_guide.md:329`](Dashboard_guide.md#L329) — "KB ingestion job is NOT auto-triggered by the processing pipeline yet."
- [`api_handler.py:175`](../Infra/functions/api_handler/api_handler.py#L175) — `POST /scan` exists for the manual UI button.

**Gaps**
- No `s3:ObjectCreated` notifications on raw or processed buckets.
- KB ingestion is a manual CLI step.
- No completion-event chain (`processing → KB ingest → scan`).
- Upload-to-detection latency is 0–24h, not "as they happen."

**Risk if shipped as-is** — Users uploading documents see no new conflicts for up to 24h until the next cron scan, breaking the "as they happen" promise.

### F2 — CR creation & approval workflow

**Current state**
- [`api_handler.py:914-935`](../Infra/functions/api_handler/api_handler.py#L914) — `_build_approver_chain` implements DEV/STAGING/PRE_PROD/PROD tiers + legal notify for CRITICAL/HIGH.
- [`api_handler.py:955-1007`](../Infra/functions/api_handler/api_handler.py#L955) — `POST /actions` writes CR + `CR_CREATED` audit.
- [`api_handler.py:1063-1072`](../Infra/functions/api_handler/api_handler.py#L1063) — CISO override marks all PENDING approvers approved in one click.
- [`api_handler.py:1107-1119`](../Infra/functions/api_handler/api_handler.py#L1107) — execute updates linked finding to `RESOLVED`.
- [`useApi.js:70-168`](../ui/src/hooks/useApi.js#L70) — every transition (`approve`/`reject`/`execute`/`escalate`) calls `load()` to refresh UI without page reload.
- [`ActionCenter.jsx:234-250`](../ui/src/pages/ActionCenter.jsx#L234) — UI wires all four transition buttons through `useChangeRequests`.

**Gaps** — none identified.

**Risk if shipped as-is** — N/A.

### F3 — Compliance reporting

**Current state**
- [`Governance.jsx:16-62`](../ui/src/pages/Governance.jsx#L16) — PCI-DSS v4.0, NAIC MDL-668, SOC 2, ISO 27001:2022 frameworks defined with per-UC mapping.
- [`Governance.jsx:122-147`](../ui/src/pages/Governance.jsx#L122) — `evalControl()` / `liveScore()` derive scores live from open findings + severity penalties.
- [`Findings.jsx:58-67`](../ui/src/pages/Findings.jsx#L58) — CSV export only; no PDF.
- [`mockData.js:5-327`](../ui/src/mockData.js#L5) — only UC04/05/07/08/09/10 carry `regulatory` tags; UC01-03, UC06, UC11-12 empty.
- [`scan_rule_pack.py:64-359`](../agents/master_orchestrator/scan_rule_pack.py#L64) — same coverage gap in the real scan rule pack.
- No `/export` or `/reports` endpoint in [`api_handler.py:166-711`](../Infra/functions/api_handler/api_handler.py#L166).

**Gaps**
- ~50% of UCs have no regulatory framework tag.
- SOC 2 / ISO 27001 controls are partially orphaned (CC7.2, CC8.1, A.5.36 have no linked finding).
- No PDF export, no scheduled report Lambda, no attestation/sign-off capture.
- Scan path still uses hardcoded specialist observations.

**Risk if shipped as-is** — The demo can't credibly claim live compliance reporting across the four frameworks; users can filter in UI but can't produce auditor-grade evidence.

### F4 — Analyst chat over live findings

**Current state**
- [`scanner_lambda.py:82-166`](../Infra/functions/scanner/scanner_lambda.py#L82) — real scan pipeline writes to `conflicts-v2` + `scan-runs`.
- [`agent.py:329-338`](../agents/master_orchestrator/agent.py#L329) — Master has only `sharepoint_lookup`, `awsconfig_lookup`, `zscaler_lookup`.
- [`agent.py:455-495`](../agents/master_orchestrator/agent.py#L455) — chat branch never queries `conflicts-v2`.
- [`AnalystView.jsx:473-486`](../ui/src/pages/AnalystView.jsx#L473) — `get_active_conflicts` shown in the UI tool list but not implemented.

**Gaps**
- No tool to query `conflicts-v2` by severity / domain / scan_run_id / conflict_id.
- No tool to query `scan-runs` for execution history.
- Chat can't answer "show me all critical findings from the last scan" grounded in actual data.
- UI promises a tool that doesn't exist.

**Risk if shipped as-is** — Analyst asks "summarise open critical findings" and gets generic KB-grounded prose, missing the actual scan context — undermines the core analyst use case.

## 3. Master step-by-step plan

Consolidated across all four audits. Steps are ordered for dependency: data/UI corrections first, then the F1 event chain, then F4 (chat access to findings), then F3 (reporting on top of richer tags + shared DDB access).

| # | Step | Files | Effort | Features |
|---|---|---|---|---|
| 1 | Backfill `regulatory` arrays for UC01-03/06/11/12 so framework filters/scorecards have full coverage | [mockData.js](../ui/src/mockData.js), [scan_rule_pack.py](../agents/master_orchestrator/scan_rule_pack.py) | SMALL | F3 |
| 2 | Replace `get_active_conflicts` text in AnalystView with the actual tool list (sharepoint/awsconfig/zscaler) | [AnalystView.jsx:473](../ui/src/pages/AnalystView.jsx#L473) | SMALL | F4 |
| 3 | Add S3 `EventBridgeNotification` on `RawBucket` for `s3:ObjectCreated:*`; wire EventBridge rule → `processing_pipeline` | [04-storage.yaml](../Infra/templates/04-storage.yaml), [05-compute.yaml](../Infra/templates/05-compute.yaml) | SMALL | F1 |
| 4 | Extend `processing_pipeline` to call `bedrock-agent start_ingestion_job` after copying files to `ProcessedBucket` | [processing_pipeline.py](../Infra/functions/processing_pipeline/processing_pipeline.py), [05-compute.yaml](../Infra/templates/05-compute.yaml) | MEDIUM | F1 |
| 5 | Add EventBridge rule on Bedrock ingestion-job-complete (or short poller) → invoke `scanner_lambda` | [11-scanner.yaml](../Infra/templates/11-scanner.yaml), [scanner_lambda.py](../Infra/functions/scanner/scanner_lambda.py) | MEDIUM | F1 |
| 6 | Grant Master Orchestrator role read on `conflicts-v2` + `scan-runs`; pass `CONFLICTS_TABLE_V2` / `SCAN_RUNS_TABLE` env vars | [09-agentcore.yaml](../Infra/templates/09-agentcore.yaml), [agent.py](../agents/master_orchestrator/agent.py) | MEDIUM | F4 |
| 7 | Add `@tool query_conflicts(scan_run_id?, severity?, domain?, conflict_id?)` and `@tool query_scan_runs(limit?)` to Master | [agent.py](../agents/master_orchestrator/agent.py) | MEDIUM | F4 |
| 8 | Update Master `SYSTEM_PROMPT` to prefer the new conflicts/scan tools when answering finding/compliance questions | [agent.py](../agents/master_orchestrator/agent.py) | SMALL | F4 |
| 9 | Persist `scan_run_id` referenced during chat turns into AgentCore Memory / audit row via `_save_turn()` | [agent.py](../agents/master_orchestrator/agent.py) | SMALL | F4 |
| 10 | Add `reportlab` to Lambda requirements and ship via layer or bundled deps for `api_handler` | [requirements-lambda.txt](../Infra/requirements-lambda.txt), [06-api.yaml](../Infra/templates/06-api.yaml) | SMALL | F3 |
| 11 | Implement `GET /export/compliance?framework=&format=csv\|pdf` in API handler | [api_handler.py](../Infra/functions/api_handler/api_handler.py) | MEDIUM | F3 |
| 12 | Add per-framework export buttons on Findings + Governance pages | [Findings.jsx](../ui/src/pages/Findings.jsx), [Governance.jsx](../ui/src/pages/Governance.jsx) | SMALL | F3 |
| 13 | Add attestation fields (`attested_by`, `attested_at`, `attestation_framework`) to scan-runs + "Attest Compliance" CTA | [04-storage.yaml](../Infra/templates/04-storage.yaml), [Dashboard.jsx](../ui/src/pages/Dashboard.jsx), [api_handler.py](../Infra/functions/api_handler/api_handler.py) | MEDIUM | F3 |
| 14 | New `compliance_report_generator` Lambda on monthly EventBridge cron → iterate frameworks, write PDFs to `s3://…/compliance-reports/`, SNS notify | [Infra/functions/compliance_report_generator/handler.py](../Infra/functions/compliance_report_generator/), [12-compliance-reports.yaml](../Infra/templates/12-compliance-reports.yaml), [02-security.yaml](../Infra/templates/02-security.yaml) | MEDIUM | F3 |
| 15 | Replace `_seed_sharepoint/zscaler/awsconfig` synthetic fixtures with live KB + Zscaler JSON queries in scan path | [master/agent.py](../agents/master_orchestrator/agent.py) + 3 specialists | LARGE | F3, F4 |
| 16 | Update Dashboard_guide.md and scan-flow-path.md to reflect the new S3-event chain, chat tools, and reporting endpoints | [Dashboard_guide.md](Dashboard_guide.md), [scan-flow-path.md](scan-flow-path.md) | SMALL | F1, F3, F4 |

### Sequence rationale

Steps 1-2 are pure data/UI corrections that visibly improve the demo before any infra change. Steps 3-5 form the F1 upload-to-detection chain and should land together because step 5 only makes sense once step 4 writes new docs into the KB. Steps 6-9 unlock F4 and must precede F3's reporting work (step 15) because grounded chat is a smaller change and the same `conflicts-v2` access pattern informs the export endpoint. Steps 10-14 build the F3 reporting stack on top of richer tags (step 1) and shared DDB access (step 6). Step 15 (LARGE) replaces synthetic specialist data — deferred until everything around it can absorb richer fixtures. Step 16 closes documentation drift last.

## 4. Quick wins (<30 minutes each, all from §3)

Picked because they visibly move the demo forward with no AWS deploys.

| # | Quick win | Lands in |
|---|---|---|
| 1 | Backfill `regulatory` arrays in `mockData.js` and `scan_rule_pack.py` so the Governance scorecard stops orphaning SOC 2 / ISO 27001 controls | UI immediately (via Vite HMR); rule-pack change ships next time the Master image is rebuilt |
| 2 | Replace the `get_active_conflicts` line in `AnalystView.jsx:473` so the UI stops advertising a tool that doesn't exist | UI immediately |
| 3 | Update Master `SYSTEM_PROMPT` to reference the upcoming conflicts/scan tools — wording lands before the tools deploy | Code only until next Master rebuild |
| 4 | Flip the "KB ingestion is NOT auto-triggered" note in `Dashboard_guide.md:329` to a TODO with the planned chain | Immediately |
| 5 | Add disabled "Export PDF" buttons on `Findings.jsx` and `Governance.jsx` with tooltip "available after backend ships" | UI immediately |

## 5. Out of scope (explicitly deferred)

- **Step 15 — replacing synthetic specialist observations in scan path (LARGE)** — recommended by F3 and F4 audits, kept in backlog but explicitly deprioritized: the scan pipeline already produces deterministic, demo-stable findings; swapping to live KB/Zscaler queries jeopardizes demo reliability and is independent of the reporting + chat improvements that close the same user-facing gaps.
- **SNS distribution list for monthly reports (part of step 14)** — recipient management is an ops decision, not a code one; ship the PDF-to-S3 half and defer SNS until distribution targets are confirmed.
- **Polling fallback if Bedrock ingestion-complete EventBridge event is unavailable (alternative to step 5)** — only build the polling variant if the native event source proves unreliable in dev; avoids carrying two code paths.
