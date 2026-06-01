# ARBITER ST21 Dashboard & Scanner — Design, Development, and Testing Guide

This is the operating guide for the dashboard + autonomous scanner pipeline added to the ARBITER ST21 POC. It is the canonical reference for what was built, why, and how to extend or re-run it.

> Source documents: [`BaselineFiles/ARBITER-POC-Personas-and-User-Flows-V1.0.docx`](../BaselineFiles/ARBITER-POC-Personas-and-User-Flows-V1.0.docx) and [`BaselineFiles/ARBITER-POC-Scope-and-Use-Cases-V1.0.docx`](../BaselineFiles/ARBITER-POC-Scope-and-Use-Cases-V1.0.docx). Architecture diagram: [`arbiter_st21_dashboard_architecture.drawio`](arbiter_st21_dashboard_architecture.drawio).

## 1. Problem framing

The docs describe a multi-persona compliance product with a Governance Overview, a Domain × Source heat map, 12 named use cases (ARBITER-UC01..UC12), and a chatbot drill-down. The prior build delivered the personas (Cognito groups + Persona context) and a chat surface, but the dashboard was static, the heat map was a topology diagram (not the doc-described matrix), the `/scan` and `/actions/*` API routes were stubs, and the Master Orchestrator returned free-text only — nothing wrote structured findings to DynamoDB.

This delivery closes those gaps end-to-end so a GRC analyst can press **Run AI Scan** and see 12 conflicts materialise from real KB content and a deterministic rule-pack within ~45 seconds.

## 2. Personas and what each one expects

| Persona | Group | Primary route | What the dashboard surfaces for them |
|---|---|---|---|
| Sarah Chen (Employee) | `employee` | `/analyst` | Single tile → chat with cited policy answers (UC01..UC12 Q&A) |
| Marcus Webb (SOC) | `soc` | `/findings` | Recent Activity feed surfaced above KPIs; clickable into Finding Detail |
| Priya Nair (GRC) | `grc` | `/findings` | Full Governance Overview: Run Scan, KPIs, Heat Map, Trend, Activity |
| Diana Osei (CISO) | `ciso` | `/actions` | Pending CRs surfaced first; one-click approve/reject |

Persona reordering is implemented in `Dashboard.jsx` via `usePersona()`. The Cognito group claim drives `personaId` ([`ui/src/contexts/PersonaContext.jsx`](../ui/src/contexts/PersonaContext.jsx)); no in-app persona switching.

## 3. The 12 use cases at a glance

| UC | Title | Severity | Conflict type | Domain | Source pair |
|---|---|---|---|---|---|
| UC01 | Dropbox approved in policy but blocked by Zscaler | HIGH | CONTRADICTION | Access Mgmt | SharePoint + Zscaler |
| UC02 | Remote support tools blocked for vendors | HIGH | CONTRADICTION | Vendor Mgmt | SharePoint + Zscaler |
| UC03 | Firefox blocked despite browser-freedom policy | MEDIUM | CONTRADICTION | Access Mgmt | SharePoint + Zscaler |
| UC04 | SSL inspection bypassed for finance domains | CRITICAL | GAP | Compliance | SharePoint + Zscaler |
| UC05 | MFA enforced only on admins | CRITICAL | GAP | Access Mgmt | SharePoint + Zscaler |
| UC06 | IoT in monitor-only mode (policy requires block) | HIGH | GAP | Network Security | SharePoint + Zscaler |
| UC07 | Production ALB exposed without WAF | CRITICAL | DRIFT | Cloud Security | SharePoint + AWS Config |
| UC08 | Dev-to-Prod VPC peering active | CRITICAL | DRIFT | Network Security | SharePoint + AWS Config |
| UC09 | S3 customer data replicating to eu-west-1 | CRITICAL | DRIFT | Data Governance | SharePoint + AWS Config |
| UC10 | DLP blocks authorised actuarial transfers | HIGH | CONTRADICTION | Data Governance | SharePoint + Zscaler |
| UC11 | ZTNA geo-restriction blocks 6 approved countries | MEDIUM | CONTRADICTION | Vendor Mgmt | SharePoint + Zscaler |
| UC12 | Social-media blanket block ignores 4 dept exemptions | MEDIUM | GAP | Access Mgmt | SharePoint + Zscaler |

Plus 14 compliant alignments (one or two per UC) the scanner records as evidence of working controls. Heat map cells expose totals.

## 4. Architecture

```
Personas → CloudFront/S3 SPA → Cognito Hosted UI
                                 ↓
                        API Gateway REST  ◄── Cognito JWT authorizer
                        + Function URL (/chat)
                                 ↓
                       api_handler Lambda (PrivateSubnet1)
                          ↓        ↓         ↓        ↓
                     DDB tables  AgentCore  S3   scanner_lambda (async invoke)
                          ↑                            ↓
                          │                  Master Orchestrator (scan mode)
                          │                    ↳ sharepoint_specialist
                          │                    ↳ awsconfig_specialist
                          │                    ↳ zscaler_specialist
                          │                            ↓
                          │                       Bedrock KB → OSS
                          │
                  EventBridge cron(0 6 * * ? *) → scanner_lambda
                                                       ↓
                                              dual-writes to:
                                              conflicts-v2, scan-runs, audit-log
```

**Key decisions:**

- **Scan-mode on the existing Master Orchestrator.** A new AgentCore runtime would duplicate IAM, VPC, and ECR plumbing without buying isolation we need. Master branches on `payload["scan"] is True` before constructing the Strands chat agent.
- **Deterministic rule-pack on top of LLM.** Each UC is a Python function in [`agents/master_orchestrator/scan_rule_pack.py`](../agents/master_orchestrator/scan_rule_pack.py) that asserts policy clauses + enforcement evidence and emits a structured `Finding`. The LLM is used only to render the `explanation` field and a `fp_score` confidence check. The demo cannot tolerate a flaky LLM missing UC09 on stage.
- **Dual-table migration on `conflicts`.** Original table has `PK conflict_id + SK detected_at` — multi-row per UC every scan. New `conflicts-v2` table uses `PK conflict_id` only, so the scanner re-runs upsert in place. Both tables are written during the transition; reads flip to v2 once seed validation passes.
- **JIRA via Atlassian MCP in the Analyst chat path.** The Lambda surface returns a mock ticket key (`MIG-MOCK-NNNN`) for the ActionCenter button. Real ticket creation happens through the user's installed Atlassian MCP plugin when invoked via the Analyst chat. Documented production upgrade: Secrets Manager + `jira-python`.

## 5. Data model

### `conflicts-v2` (new, idempotent)

```
PK: conflict_id     "ARBITER-UC04"   (stable per UC)
GSIs:
  severity-detected-index   (severity HASH, detected_at RANGE)
  domain-detected-index     (domain HASH, detected_at RANGE)
  scan_run-index            (scan_run_id HASH, conflict_id RANGE)
```

Row shape includes (in addition to all the original fields):

| Field | Values |
|---|---|
| `conflict_type` | CONTRADICTION / GAP / DRIFT / OVERLAP |
| `domain` | ACCESS_MGMT / NETWORK_SECURITY / DATA_GOVERNANCE / CLOUD_SECURITY / COMPLIANCE / VENDOR_MGMT |
| `policy_citations` | `[{doc, version, section, quote, confidence}]` |
| `enforcement_evidence` | `[{source, rule_id_or_resource_id, action, observed_at, raw:{...}}]` |
| `regulatory` | `["PCI DSS 4.0 Req 4.1", "NAIC MDL-668", ...]` |
| `scan_run_id`, `rule_key` (`"UC04"`), `fp_score` (Decimal), `compliant` (bool), `explanation` (cached) | |
| `detected_at` | ISO string attribute (not a key) |

### `scan-runs` (new)

```
PK: scan_run_id      "scan-2026-05-29T06:00:00Z-a1b2"
SK: started_at
GSI by-status        (status HASH, started_at RANGE)  — dashboard polling + trend
TTL: 30 days
```

Attributes: `status RUNNING|COMPLETED|FAILED`, `triggered_by` (`schedule` or `manual:<sub>`), `totals` (per-severity counts), `source_versions`, `rule_pack_version`, `duration_ms`, `finished_at`, `error`.

### `change-requests` (attribute additions, no schema change)

`linked_conflict_id`, `approvers_state`, `state_transitions[]`, `jira_ticket_key`, `execution_log[]`.

### `audit-log` (no schema change)

New `action_type` values: `SCAN_STARTED`, `SCAN_COMPLETED`, `SCAN_FAILED`, `CR_CREATED`, `CR_APPROVED`, `CR_REJECTED`, `CR_EXECUTED`, `CR_ESCALATED`, `JIRA_LINKED`, `INGESTION_COMPLETE`.

## 6. Scanner pipeline

### Trigger surfaces

- **Scheduled:** EventBridge `cron(0 6 * * ? *)` UTC (02:00 PST) → `scanner_lambda`.
- **Ad-hoc:** `POST /scan` from the UI Run Scan button → `api_handler` async-invokes `scanner_lambda` via `lambda:Invoke(InvocationType="Event")` → immediately returns `{scan_run_id, status:"RUNNING"}`.
- **Local:** `scripts/run_scanner_local.py` invokes the Master runtime ARN directly with `{scan:true,...}` payload for development.

### Scanner Lambda flow

```
handler(event):
  scan_run_id = f"scan-{now_iso}-{uuid8}"
  put scan-runs (status=RUNNING)
  put audit-log (SCAN_STARTED)
  try:
    resp = agentcore.invoke_agent_runtime(MASTER_ARN, payload={"scan": True, ...})
    findings = json.loads(resp["response"].read())["findings"]
    with conflicts_v2.batch_writer() as bw:
      for f in findings:
        bw.put_item(Item={**f, "scan_run_id": scan_run_id, "detected_at": now_iso})
    update scan-runs (status=COMPLETED, totals=aggregate(findings))
    put audit-log (SCAN_COMPLETED)
  except Exception as e:
    update scan-runs (status=FAILED, error=str(e))
    put audit-log (SCAN_FAILED)
    raise
```

Idempotency: `conflict_id = f"ARBITER-{rule_key}"` is stable; same scan rewrites the row. False-positive guard: two compliant resources (ALB-with-WAF, S3 replicating us-east-1→us-west-2) are injected into the AWS Config snapshot; the scanner must record them as compliant and NOT as conflicts.

### Master Orchestrator scan-mode

[`agents/master_orchestrator/agent.py`](../agents/master_orchestrator/agent.py)'s `BedrockAgentCoreApp` entrypoint inspects payload:

```python
if payload.get("scan") is True:
    return run_scan(payload)
else:
    return run_chat(payload)
```

`run_scan()`:
1. Fans out to 3 specialists with a new `produce_findings(domain_filter, source_version)` tool that returns structured JSON (not free text).
2. Loads [`agents/master_orchestrator/scan_rule_pack.py`](../agents/master_orchestrator/scan_rule_pack.py). 12 matcher functions each return a `Finding | None`.
3. Per finding, single prompt-cached LLM call generates `explanation` + `fp_score`.
4. Returns `{"findings": [...]}` as JSON.

## 7. API surface

`api_handler.py` route table (additions in BOLD):

| Method | Path | Action |
|---|---|---|
| GET | `/findings` | List with `?severity=` / `?status=` (existing) |
| **GET** | `/findings/{conflict_id}` | Single finding for the Detail view |
| POST | `/chat` | Existing — invokes Master via Function URL |
| **POST** | `/scan` | Async-invokes `scanner_lambda`; returns `{scan_run_id, status:"RUNNING"}` |
| **GET** | `/scan-runs` | Last 10 runs (for trend + last-scan tile) |
| **GET** | `/scan-runs/{id}` | Single run (UI polling) |
| GET | `/actions` | List CRs (existing) |
| **POST** | `/actions` | Create CR linked to `conflict_id` |
| **POST** | `/actions/{cr_id}/approve` | Flip approver state |
| **POST** | `/actions/{cr_id}/reject` | Mark REJECTED |
| **POST** | `/actions/{cr_id}/execute` | Run simulated execution; mark conflict RESOLVED |
| **POST** | `/actions/{cr_id}/escalate` | Mark ESCALATED |
| GET | `/audit` | List audit rows (existing) |
| GET | `/conversations`, `/conversations/{id}/messages` | Existing |
| GET | `/uploads/*` | Existing |
| **GET** | `/dashboard` | Aggregate for the Governance Overview (1 round-trip) |
| **GET** | `/mcp-health` | Ping configured MCP endpoints, return UP/DOWN + latency |
| **POST** | `/jira/tickets` | Stub returning `mock_ticket_key`; audit `JIRA_LINKED` |

The catch-all stub at [`Infra/functions/api_handler/api_handler.py:151`](../Infra/functions/api_handler/api_handler.py#L151) returns `{"status":"stub",...}` for any unknown route — new handlers are pure additions before that line.

### `GET /dashboard` aggregate shape

```json
{
  "kpis": {
    "policies_indexed": 5,
    "active_conflicts": {"CRITICAL":4,"HIGH":4,"MEDIUM":4,"LOW":0},
    "pending_approvals": 2,
    "mcp_health": "UP"
  },
  "heatmap": {
    "rows": ["Access Mgmt","Network Security","Data Governance","Cloud Security","Compliance","Vendor Mgmt"],
    "cols": ["SharePoint+Zscaler","SharePoint+AWS Config"],
    "cells": [[3,0],[1,2],[1,1],[0,1],[1,0],[2,0]]
  },
  "last_scan": {"scan_run_id":"...","finished_at":"...","totals":{...}},
  "recent_activity": [/* 5 newest audit rows */],
  "trend": [{"date":"2026-05-22","open":18}, /* 30 buckets */]
}
```

## 8. UI

### Routes

[`ui/src/App.jsx`](../ui/src/App.jsx) — new route `/findings/:id` for the FindingDetail page (RBAC: grc, soc, ciso). All other routes unchanged.

### Pages

| Page | Change |
|---|---|
| [`pages/Dashboard.jsx`](../ui/src/pages/Dashboard.jsx) | Replaced with Governance Overview: header + Run AI Scan, 4 KPI tiles, Heat Map (6×2 Tailwind grid, click-to-drill), recharts 30-day trend line, Recent Activity feed (top 5 audit rows). Persona-based reordering via `usePersona()`. |
| [`pages/FindingDetail.jsx`](../ui/src/pages/FindingDetail.jsx) | NEW — 3-column split-screen: Policy citations / Enforcement evidence / Explanation+remediation+Create CR. |
| [`pages/HeatMap.jsx`](../ui/src/pages/HeatMap.jsx) | Tabbed: "Domain × Source matrix" (default) + "System Topology" (existing SVG). |
| [`pages/Findings.jsx`](../ui/src/pages/Findings.jsx) | New filters (domain, conflict_type, compliance framework); CSV Export button; row click → `/findings/:id`. |
| [`pages/ActionCenter.jsx`](../ui/src/pages/ActionCenter.jsx) | Live `useChangeRequests()` wired to real endpoints; "Open JIRA ticket" button next to Create CR. |
| [`pages/MCPChat.jsx`](../ui/src/pages/MCPChat.jsx) | Static `MCP_SERVERS` replaced with live `GET /mcp-health` polled every 30s. |

### Hooks

[`ui/src/hooks/useApi.js`](../ui/src/hooks/useApi.js) — new: `useDashboard()` (60s polling), `useScanTrigger()` (POST /scan + returns id), `useScanRuns()`, `useFindingDetail(id)`, `useMcpHealth()` (30s), `createJiraTicket()`. Extended `useChangeRequests()` to wire approve/reject/execute/escalate to live endpoints with mock fallback.

### Mock data + helpers

[`ui/src/mockData.js`](../ui/src/mockData.js) — extended each UC with the new fields (`conflict_type`, `domain`, structured `policy_citations` and `enforcement_evidence`) without removing the original ones (backward compat for any code paths still reading the flat strings). New `MOCK_COMPLIANT` exporting 14 rows. New helper `buildDomainSourceMatrix(findings)` for the doc-mandated Domain × Source grid (the original `buildConflictMatrix()` still computes Source × Severity).

## 9. CloudFormation

| Template | Change |
|---|---|
| [`Infra/templates/04-storage.yaml`](../Infra/templates/04-storage.yaml) | Add `ConflictsTableV2` (PK only) + 3 new GSIs + `ScanRunsTable` (PK+SK + by-status GSI + TTL 30d). Keep original `ConflictsTable` for dual-write window. |
| [`Infra/templates/06-api.yaml`](../Infra/templates/06-api.yaml) | Add SAM Events for `GET /scan-runs`, `GET /scan-runs/{id}`, `GET /dashboard`, `GET /mcp-health`, `POST /jira/tickets`. Existing CFN Events for `/scan`, `/findings/{id}`, `/actions/*` are unchanged — only the handler body needed implementation. New env vars: `CONFLICTS_TABLE_V2`, `SCAN_RUNS_TABLE`, `SCANNER_LAMBDA_NAME`, `MCP_ENDPOINTS`. |
| [`Infra/templates/11-scanner.yaml`](../Infra/templates/11-scanner.yaml) | NEW — `ScannerLambda` (Python 3.13, 600s timeout, 1GB, PrivateSubnet1, LambdaSG) + EventBridge schedule. IAM least-privilege: `bedrock-agentcore:InvokeAgentRuntime` on Master only; DDB writes on conflicts-v2/scan-runs/audit-log. Output: `ScannerLambdaArn`. |
| [`Infra/templates/02-security.yaml`](../Infra/templates/02-security.yaml) | `lambda:InvokeFunction` on `ScannerLambdaArn` added to `ApiHandlerRole`. |
| [`Infra/deploy.sh`](../Infra/deploy.sh) | `CF_STACKS_POST` extended with `11-scanner`. SAM stacks already read `params/dev.json` (from a prior session's fix), so new params flow through automatically. |

Stack order: `00-bootstrap → 01-network → 02-security → 03-identity → 04-storage → 05-compute (SAM) → 06-api (SAM) → 09-agentcore → 10-ui-hosting → 11-scanner`.

## 10. Demo corpus regeneration

`scripts/generate_baseline_corpus.py` (NEW) produces:

1. **5 MIG-POL markdown sources** under `BaselineFiles/_source/` rendered to PDFs in `BaselineFiles/` (originals backed up to `BaselineFiles/_archive/`). Each file contains the verbatim clauses the use-case doc quotes (e.g., MIG-POL-001 §2.1 `"Dropbox Business listed as approved. Passed vendor assessment Q3 2025."`, MIG-POL-002 §2.2 SSL inspection, MIG-POL-004 §3 VPC peering prohibition).
2. **Zscaler ruleset JSON** under `BaselineFiles/zscaler/` enumerating every cited rule (`ZIA-URLCAT-CLOUD-BLK-042`, `ZIA-SSL-BYPASS-FIN-DOMAINS`, `ZPA-AUTHPOL-ADMIN-MFA-ONLY`, etc.).
3. **AWS Config snapshot** under `BaselineFiles/aws-config/by-resource-type/` listing the 3 non-compliant resources + 2 compliant guard resources.
4. `aws s3 sync` to `s3://dev-st21arbiter-poc-processed/baseline/` then `aws bedrock-agent start-ingestion-job` (poll for COMPLETE, ~60s).

`scripts/seed_mock_data.py` (extended) inserts 12 conflicts (with new schema), 14 compliant rows, 2 CRs, 8 audit rows, 1 `scan-runs` row (`scan_run_id="seed-bootstrap"`). Dual-writes to both `conflicts` and `conflicts-v2`.

## 11. JIRA / MCP

Two-path design:

- **Analyst chat (real ticket creation):** Master gets a `jira_create_ticket(summary, description, project_key, severity)` tool. When invoked through the Analyst chat surface in a Claude session with the Atlassian MCP plugin installed (`mcp__plugin_atlassian_atlassian__*`), the call hits real Atlassian Cloud and returns a real ticket key.
- **ActionCenter button (mock):** `POST /jira/tickets` from the Lambda returns `{status:"not_implemented_in_lambda", mock_ticket_key:"MIG-MOCK-NNNN"}` and writes an audit `JIRA_LINKED` row. UI displays the mock key with a tooltip explaining the dual-path design.

**Production upgrade path (documented, not built):** add `JiraSecretArn` CFN parameter → Secrets Manager → `secretsmanager:GetSecretValue` IAM grant → `jira-python` REST PUT.

## 12. Testing

### Unit

- **Lambda (pytest):** `tests/scanner/test_rule_pack.py` — per UC, feed synthetic specialist outputs, assert finding emitted with correct severity / conflict_type / domain. Negative tests confirm each UC is suppressed when its trigger condition is absent. The existing `Infra/functions/api_handler/test_api.py` pattern applies.
- **UI (vitest):** [`ui/src/__tests__/helpers.test.js`](../ui/src/__tests__/helpers.test.js) extended for `buildDomainSourceMatrix()`, dashboard KPI memo, CSV export builder.

### Integration

- `scripts/run_scanner_local.py` (NEW) invokes the Master runtime ARN with `{scan:true,...}` directly and asserts the response is a 26-element array (12 conflicts + 14 compliant), all UC keys present.
- API smoke test (Postman collection in [`Documents/API_Usage.md`](API_Usage.md)): `POST /scan` → poll `GET /scan-runs/{id}` → `GET /dashboard` → assert KPI counts equal seed values.

### End-to-end demo script

1. Sign in as `grc_priya@…` → land on Governance Overview, see KPIs from the seeded scan.
2. Click **Run AI Scan** → spinner; UI polls every 2s; "Scan complete: 12 conflicts, 14 compliant" within ~45s. Heat map populates.
3. Click the SSL-inspection cell → Findings filters to UC04 → row click → split-screen detail with MIG-POL-002 §2.2 quote on left, `ZIA-SSL-BYPASS-FIN-DOMAINS` JSON on right.
4. Click **Create CR** → modal submits → Action Center shows pending CR.
5. Sign out → sign in as `ciso_daiana@…` → Governance Overview surfaces Action Center → approve CR → status transitions; audit written.
6. Sign in as `soc_marcus@…` → Recent Activity feed shows the `CR_APPROVED` row at top.
7. Sign in as `emp_sarah@…` → Employee-stripped overview → `/analyst`: "Why is Dropbox blocked?" → reply cites MIG-POL-001 §2.1 + `ZIA-URLCAT-CLOUD-BLK-042`.
8. Repeat UC02..UC12 chat probes — citations must match the regenerated corpus.

### False-positive guard

Inject a compliant ALB-with-WAF resource into the AWS Config snapshot. Run scanner. Assert exactly 14 compliant rows AND the WAF-equipped ALB does NOT appear in the conflicts.

## 13. Implementation sequencing (and how to reproduce)

The plan was executed in 6 demo-safe steps. Each is independently verifiable.

| Step | Work | Demo gate |
|---|---|---|
| 1 | Seed extension + Dashboard rebuild + FindingDetail + HeatMap tabs | Full dashboard works on seed data, no agent changes |
| 2 | `04-storage` (V2 + scan-runs) + `06-api` aggregate routes | Live KPIs + live heat map from DDB |
| 3 | Scanner pipeline: rule pack + Master scan-mode + `scanner_lambda` + `11-scanner` + `POST /scan` | Ad-hoc scan produces real findings |
| 4 | CR workflow: POST `/actions` + approve/reject/execute/escalate | GRC creates CR → CISO approves → conflict marked RESOLVED |
| 5 | JIRA via MCP: tool + stub endpoint + ActionCenter button | "Open JIRA" returns mock key; Analyst chat → real ticket via MCP |
| 6 | Polish: recharts trend + persona reorder + corpus regen + KB ingestion | All success criteria met; demo script clean end-to-end |

Reproduction:

```bash
cd Infra && ./deploy.sh        # All stacks
cd ../scripts
source .venv/bin/activate
python3 generate_baseline_corpus.py     # PDFs + JSON + S3 sync + KB ingestion
python3 seed_mock_data.py               # 12+14+2+8+1 to DDB
KB_ID=<...> GUARDRAIL_ID=<...> MASTER_MEMORY_ID=<...> AWS_REGION=us-east-1 python3 deploy_agents.py   # 4 agent images
cd ../ui && npm install && npm run build && npm run dev      # local UI
```

## 14. Operational notes

- Function URL CORS for `/chat` is set via the `ChatFunctionUrlAllowedOrigins` parameter in `Infra/params/dev.json` (from a prior session). When CloudFront is redeployed and gets a new domain, update that param and re-deploy `06-api`.
- AgentCore Runtime only supports AZ IDs `use1-az1/2/4`. `PrivateSubnet2` is pinned to `us-east-1b` (= `use1-az1` in this account) specifically for AgentCore.
- Default foundation model: Amazon Nova 2 Lite (no Marketplace subscription). Override per runtime by setting `MASTER_MODEL_ID=...` on `deploy_agents.py`.
- `deploy_agents.py` builds via CodeBuild Graviton — never pass `--skip-build` on a fresh account.
- **F1 auto-detect (LIVE):** Uploading a new file to `s3://${env}-${project}-raw/` automatically triggers the chain `s3:ObjectCreated → EventBridge → processing_pipeline → raw→processed move → bedrock:StartIngestionJob → poll until COMPLETE → scanner_lambda async-invoke`. Measured end-to-end latency in dev: ~30 seconds from upload to a fresh `scan-runs` row with `triggered_by=auto-ingest:<key>`. Wired via Steps 3-5 of [`Documents/Feature_Coverage_Plan.md`](Feature_Coverage_Plan.md#3-master-step-by-step-plan):
  - [`Infra/templates/04-storage.yaml`](../Infra/templates/04-storage.yaml) — `RawBucket.NotificationConfiguration.EventBridgeConfiguration` fires events to the default bus.
  - [`Infra/templates/05-compute.yaml`](../Infra/templates/05-compute.yaml) — `RawBucketObjectCreatedRule` filters those events (excluding `File_Transfer_Reports/`) and targets `processing_pipeline`.
  - [`Infra/templates/02-security.yaml`](../Infra/templates/02-security.yaml) — `ProcessingPipelineRole` granted `bedrock:StartIngestionJob` + `lambda:InvokeFunction` on the scanner.
  - [`Infra/functions/processing_pipeline/processing_pipeline.py`](../Infra/functions/processing_pipeline/processing_pipeline.py) — `_handle_single_object_event()` does the move, `_start_kb_ingestion()` kicks the job, `_wait_for_ingestion()` polls (180s budget, 5s interval), `_invoke_scanner()` async-invokes with `triggered_by=auto-ingest:<key>`.
  - KB IDs are CFN parameters (`KbId`, `KbDataSourceId`) sourced from [`Infra/params/dev.json`](../Infra/params/dev.json); refresh after KB recreation.
  - Twice-daily batch cron (`ProcessingPipelineSchedule`) remains the safety net for files uploaded outside the event path (e.g. via versioning replays).

## 15. Where things live

| Concern | Location |
|---|---|
| CFN/SAM templates | [`Infra/templates/`](../Infra/templates/) |
| Stack parameters | [`Infra/params/dev.json`](../Infra/params/dev.json) |
| Lambda — API | [`Infra/functions/api_handler/`](../Infra/functions/api_handler/) |
| Lambda — Scanner | [`Infra/functions/scanner/`](../Infra/functions/scanner/) |
| Lambda — Processing | [`Infra/functions/processing_pipeline/`](../Infra/functions/processing_pipeline/) |
| Agent source | [`agents/`](../agents/) |
| UI source | [`ui/src/`](../ui/src/) |
| KB seed corpus | [`BaselineFiles/`](../BaselineFiles/) |
| Provisioning + seed scripts | [`scripts/`](../scripts/) |
| API reference | [`Documents/API_Usage.md`](API_Usage.md) |
| Architecture diagram (this delivery) | [`Documents/arbiter_st21_dashboard_architecture.drawio`](arbiter_st21_dashboard_architecture.drawio) |
| Architecture diagram (prior) | [`Documents/Cleaner_version.drawio`](Cleaner_version.drawio) |
