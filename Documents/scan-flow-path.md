# Run AI Scan — Flow Path

This document describes exactly what happens when the **Run AI Scan** button is clicked on the Governance Dashboard, what's real vs. synthetic in the current build, and the upgrade path to make the scan path fully live.

## What actually happens when you click Run AI Scan

```text
Browser (Dashboard)
   │  POST /scan
   ▼
API Gateway → api_handler Lambda
   │  • pre-writes RUNNING row to scan-runs (real DDB)
   │  • lambda.invoke(scanner_lambda, async)
   │  • returns scan_run_id immediately
   ▼
scanner_lambda (real Lambda, real VPC, real IAM)
   │  • write SCAN_STARTED audit row (real DDB)
   │  • agentcore.invoke_agent_runtime(
   │       arn:aws:bedrock-agentcore:.../master_orchestrator,
   │       payload={"scan": true, ...})    ← real AgentCore Runtime call
   ▼
Master Orchestrator (real Bedrock AgentCore container running on Nova 2 Lite)
   │  payload["scan"] is True  →  _run_scan(payload)
   │  ┌────────────────────────────────────────────────────────┐
   │  │ THIS is where the demo cuts a corner today:             │
   │  │ specialist observations are HARD-CODED Python dicts     │
   │  │ in agent.py (_seed_sharepoint_observations / _zscaler   │
   │  │ / _awsconfig). The 3 specialist runtimes are NOT called │
   │  │ for the scan path. Chat-mode still uses them.           │
   │  └────────────────────────────────────────────────────────┘
   │  • from scan_rule_pack import run_rule_pack
   │  • returns 12 conflicts + 14 compliant as JSON
   ▼
scanner_lambda
   │  • BatchWriteItem to conflicts-v2 (real DDB, 26 rows)
   │  • update scan-runs status=COMPLETED with totals
   │  • write SCAN_COMPLETED audit row
   ▼
UI polls GET /scan-runs/{id} → sees COMPLETED → renders the dashboard.
```

## What's real, what isn't

| Layer | Real | Synthetic / mocked |
|---|---|---|
| HTTP → Lambda → AgentCore → Lambda → DDB orchestration | ✅ | |
| Lambda IAM, VPC, KMS, EventBridge cron | ✅ | |
| Master Orchestrator container on Bedrock AgentCore Runtime | ✅ (real Nova 2 Lite call wraps the JSON serialization) | |
| [`scan_rule_pack.py`](../agents/master_orchestrator/scan_rule_pack.py) deterministic matchers (12 UC functions) | ✅ — same code path that would run against real data | |
| Conflicts persisted to DynamoDB (`conflicts-v2`) | ✅ | |
| `scan-runs` history, audit-log, KPI/heatmap/trend derived from DDB | ✅ | |
| CR workflow, approve/reject/execute, status transitions | ✅ | |
| **Specialist observations the rule pack matches against** | | ❌ Hard-coded Python dicts in [`agents/master_orchestrator/agent.py`](../agents/master_orchestrator/agent.py) — `_seed_sharepoint_observations()`, `_seed_zscaler_observations()`, `_seed_awsconfig_observations()` |
| **The 3 specialist runtimes** (sharepoint, awsconfig, zscaler) | ✅ They run, and the chat path (`/chat` → "Why is Dropbox blocked?") **does** call them and **does** retrieve from the real KB | They're **skipped** in scan-mode — the Master goes straight to the synthetic fixtures + rule pack |

## Honest summary

**Run AI Scan is a real distributed run** (every component except the inputs is live AWS infrastructure handling real data) **with synthetic source-side fixtures**. Same `scan_rule_pack.py` code, same DDB rows, same Lambda invocation pattern that production would use — just fed by hard-coded observation dicts instead of live KB / Zscaler / Config queries.

### Why it was built this way

- **Demo determinism.** The rule pack guarantees 12/12 every scan. Real LLM-driven KB retrieval would occasionally miss a citation or hallucinate a paraphrase, killing the demo on stage.
- **Step 1 of 2.** [`Documents/Dashboard_guide.md`](Dashboard_guide.md) section D explicitly called this out: each specialist gets a `produce_findings(domain, source_version)` tool that returns structured JSON. Step 1 (current) wires the orchestration end-to-end with seeds; Step 2 (future) replaces the seeds with real specialist calls.

### The chat path is fully real today

When `emp_sarah` asks "Why is Dropbox blocked?" in `/analyst`, that goes through:

`/chat` → `api_handler` → Master Orchestrator → `sharepoint_lookup` tool → **real Bedrock KB retrieve** → real Master synthesis.

The citation `"MIG-POL-001 §2.1: Dropbox Business listed as approved..."` comes from the actual markdown produced by [`scripts/generate_baseline_corpus.py`](../scripts/generate_baseline_corpus.py) and ingested into the live Bedrock KB. That's not synthetic.

## Upgrade path — swap fixtures for live calls

When you want to make Run AI Scan fully live (rule pack consuming real-source observations rather than hard-coded dicts):

| Specialist | What changes | How |
|---|---|---|
| SharePoint | Replace `_seed_sharepoint_observations()` with a `_invoke_runtime(SHAREPOINT_RUNTIME_ARN, {"produce_findings": true})` call | New tool `produce_findings(...)` on [`agents/sharepoint_specialist/agent.py`](../agents/sharepoint_specialist/agent.py) that does `kb_runtime.retrieve()` against each MIG-POL doc and returns `[{policy_doc, version, section, text, embedding_score}]` |
| Zscaler | Same pattern — replace `_seed_zscaler_observations()` | Either read the static [`BaselineFiles/zscaler/LM_ZIA_Rules_Cited.json`](../BaselineFiles/zscaler/LM_ZIA_Rules_Cited.json) from S3 (fastest), or call the real Zscaler ZIA REST API (requires `ZSCALER_API_BASE` + `ZSCALER_SECRET_ID` from Secrets Manager) |
| AWS Config | Same pattern — replace `_seed_awsconfig_observations()` | Use the existing [`agents/awsconfig_specialist/agent.py`](../agents/awsconfig_specialist/agent.py) `list_noncompliant_resources(rule_name)` tool (already calls live boto3) — wrap into a `produce_findings()` aggregator |

After this upgrade, Run AI Scan reads from the **real KB + Zscaler JSON snapshot + real AWS Config compliance data** — no fixtures left in the loop. The rule pack itself stays exactly as-is.

## Related files

- [`agents/master_orchestrator/agent.py`](../agents/master_orchestrator/agent.py) — Master entrypoint with `payload["scan"]` branch
- [`agents/master_orchestrator/scan_rule_pack.py`](../agents/master_orchestrator/scan_rule_pack.py) — the 12 deterministic UC matchers
- [`Infra/functions/scanner/scanner_lambda.py`](../Infra/functions/scanner/scanner_lambda.py) — scanner Lambda
- [`Infra/functions/api_handler/api_handler.py`](../Infra/functions/api_handler/api_handler.py) — `_handle_scan_trigger`, `_handle_get_scan_run`, `_handle_dashboard`
- [`Infra/templates/11-scanner.yaml`](../Infra/templates/11-scanner.yaml) — scanner Lambda + EventBridge cron
- [`ui/src/pages/Dashboard.jsx`](../ui/src/pages/Dashboard.jsx) — the Run AI Scan button + poll loop
- [`Documents/Dashboard_guide.md`](Dashboard_guide.md) — full system guide
- [`Documents/arbiter_st21_dashboard_architecture.drawio`](arbiter_st21_dashboard_architecture.drawio) — architecture diagram
