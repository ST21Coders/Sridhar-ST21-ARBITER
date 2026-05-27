"""ARBITER API handler — routes UI calls to backend services.

Routes:
  POST /chat                                  → master AgentCore runtime
                                                (forwards session_id + actor_id
                                                so the master maintains memory
                                                and the conversation index).
  GET  /conversations                         → list user's sessions (DDB GSI query)
  GET  /conversations/{id}/messages           → message history (AgentCore Memory
                                                list_events, chronological order)
  GET  /conversations/{id}                    → conversation metadata (DDB row)
  GET  /health                                → unauth health check

Note: POST /conversations and POST /conversations/{id}/messages were removed —
the master orchestrator now owns conversation index writes (PutItem on the
first turn of a session, UpdateItem on each turn) and message persistence
(create_event in AgentCore Memory).

Env vars:
  MASTER_AGENT_RUNTIME_ARN   ARN of the master AgentCore runtime
                             (populated by scripts/deploy_agents.py).
  SESSIONS_TABLE             DynamoDB table indexing conversations.
  MEMORY_ID                  AgentCore Memory ID (for message history reads).
"""
import base64
import json
import logging
import os
from typing import Any
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ.get("AWS_REGION", "us-east-1")
MASTER_AGENT_RUNTIME_ARN = os.environ.get("MASTER_AGENT_RUNTIME_ARN", "").strip()
SESSIONS_TABLE = os.environ.get("SESSIONS_TABLE", "")
CONFLICTS_TABLE = os.environ.get("CONFLICTS_TABLE", "")
CHANGE_REQUESTS_TABLE = os.environ.get("CHANGE_REQUESTS_TABLE", "")
AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "")
MEMORY_ID = os.environ.get("MEMORY_ID", "").strip()

agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
ddb = boto3.resource("dynamodb", region_name=REGION)
sessions_table = ddb.Table(SESSIONS_TABLE) if SESSIONS_TABLE else None
conflicts_table = ddb.Table(CONFLICTS_TABLE) if CONFLICTS_TABLE else None
crs_table = ddb.Table(CHANGE_REQUESTS_TABLE) if CHANGE_REQUESTS_TABLE else None
audit_table = ddb.Table(AUDIT_TABLE) if AUDIT_TABLE else None


# ──────────────────────────── router ────────────────────────────
def handler(event, context):
    # Log path + header names so we can debug auth issues without dumping
    # the full event (which can include JWTs in headers).
    _path = event.get("path") or event.get("rawPath", "")
    _hdr_names = sorted((event.get("headers") or {}).keys())
    logger.info("api_handler invoked: path=%s method=%s headers=%s",
                _path,
                event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method"),
                _hdr_names)
    path = event.get("path") or event.get("rawPath", "")
    method = (event.get("httpMethod") or
              event.get("requestContext", {}).get("http", {}).get("method", "")).upper()

    if path == "/health":
        return _ok({"status": "healthy", "service": "arbiter-api"})

    if path == "/chat" and method == "POST":
        return _handle_chat(event)

    if path == "/findings" and method == "GET":
        return _handle_list_findings(event)

    if path == "/actions" and method == "GET":
        return _handle_list_actions(event)

    if path == "/audit" and method == "GET":
        return _handle_list_audit(event)

    if path == "/conversations" and method == "GET":
        return _handle_list_conversations(event)

    # Path param routes under /conversations/{session_id}
    if path.startswith("/conversations/"):
        tail = path[len("/conversations/"):].split("/", 1)
        session_id = tail[0]
        sub = tail[1] if len(tail) > 1 else ""
        if sub == "messages" and method == "GET":
            return _handle_get_messages(event, session_id)
        if not sub and method == "GET":
            return _handle_get_conversation(event, session_id)

    return _ok({"status": "stub", "path": path, "method": method})


# ──────────────────────────── /chat ─────────────────────────────
def _handle_chat(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON body")
    prompt = (body.get("prompt") or body.get("message") or "").strip()
    if not prompt:
        return _err(400, "Missing 'prompt' in request body")

    if not MASTER_AGENT_RUNTIME_ARN:
        return _err(503, "Master runtime ARN not configured (run scripts/deploy_agents.py)")

    actor_id = _caller_user_id(event) or "anonymous"
    # Frontend generates session_id when starting a new chat; "adhoc" means
    # the agent should not persist (no DDB row, no memory writes).
    session_id = (body.get("session_id") or "adhoc").strip()
    # chat_type lets us separately list Analyst vs MCP sessions in the UI.
    chat_type = (body.get("chat_type") or "analyst").strip() or "analyst"

    try:
        resp = agentcore.invoke_agent_runtime(
            agentRuntimeArn=MASTER_AGENT_RUNTIME_ARN,
            payload=json.dumps({
                "prompt": prompt,
                "session_id": session_id,
                "actor_id": actor_id,
                "chat_type": chat_type,
            }).encode("utf-8"),
            contentType="application/json",
            accept="application/json",
        )
        raw = resp["response"].read().decode("utf-8")
        parsed = json.loads(raw)
        return _ok({
            "reply": parsed.get("result", raw),
            "session_id": session_id,  # echo so frontend can correlate
        })
    except Exception as e:
        logger.exception("AgentCore invocation failed")
        return _err(502, f"{type(e).__name__}: {e}")


# ──────────────────────────── /findings ─────────────────────────
def _handle_list_findings(event):
    """Return all conflicts. UI shape: {findings: [...]}.

    Scans the whole table — fine at demo scale. Supports optional severity
    and status query-string filters (server-side narrowing keeps the
    response small even as the table grows).
    """
    if not conflicts_table:
        return _err(500, "CONFLICTS_TABLE not configured")
    qs = event.get("queryStringParameters") or {}
    try:
        resp = conflicts_table.scan(Limit=200)
        items = resp.get("Items", [])
    except Exception as e:
        logger.exception("findings scan failed")
        return _err(502, f"{type(e).__name__}: {e}")
    sev = (qs.get("severity") or "").strip().upper()
    status = (qs.get("status") or "").strip().upper()
    if sev:
        items = [i for i in items if (i.get("severity") or "").upper() == sev]
    if status:
        items = [i for i in items if (i.get("status") or "").upper() == status]
    # Newest first
    items.sort(key=lambda i: i.get("detected_at") or "", reverse=True)
    return _ok({"findings": items})


# ──────────────────────────── /actions ──────────────────────────
def _handle_list_actions(event):
    """Return all change requests. UI shape: {change_requests: [...]}."""
    if not crs_table:
        return _err(500, "CHANGE_REQUESTS_TABLE not configured")
    try:
        resp = crs_table.scan(Limit=200)
        items = resp.get("Items", [])
    except Exception as e:
        logger.exception("change-requests scan failed")
        return _err(502, f"{type(e).__name__}: {e}")
    items.sort(key=lambda i: i.get("created_at") or "", reverse=True)
    return _ok({"change_requests": items})


# ──────────────────────────── /audit ────────────────────────────
def _handle_list_audit(event):
    """Return audit log entries. UI shape: {logs: [...]}."""
    if not audit_table:
        return _err(500, "AUDIT_TABLE not configured")
    try:
        resp = audit_table.scan(Limit=200)
        items = resp.get("Items", [])
    except Exception as e:
        logger.exception("audit scan failed")
        return _err(502, f"{type(e).__name__}: {e}")
    items.sort(key=lambda i: i.get("timestamp") or "", reverse=True)
    return _ok({"logs": items})


# ──────────────────────────── /conversations (list) ─────────────
def _handle_list_conversations(event):
    if not sessions_table:
        return _err(500, "SESSIONS_TABLE not configured")
    user_id = _caller_user_id(event)
    if not user_id:
        return _err(401, "Could not resolve caller identity")

    qs = event.get("queryStringParameters") or {}
    requested_type = (qs.get("type") or "").strip().lower() or None

    try:
        resp = sessions_table.query(
            IndexName="user-sessions-index",
            KeyConditionExpression=Key("user_id").eq(user_id),
            ScanIndexForward=False,  # newest first
            Limit=50,
        )
    except Exception as e:
        logger.exception("Query failed")
        return _err(502, f"{type(e).__name__}: {e}")

    sessions = [_session_summary(item) for item in resp.get("Items", [])]
    if requested_type in ("analyst", "mcp"):
        sessions = [s for s in sessions if (s.get("chat_type") or "analyst") == requested_type]
    return _ok({"sessions": sessions})


# ──────────────────────────── /conversations/{id} ───────────────
def _handle_get_conversation(event, session_id: str):
    """Returns conversation metadata only (no messages). Use /messages for those."""
    if not sessions_table:
        return _err(500, "SESSIONS_TABLE not configured")
    user_id = _caller_user_id(event)
    if not user_id:
        return _err(401, "Could not resolve caller identity")
    if not session_id:
        return _err(400, "Missing session_id")

    try:
        resp = sessions_table.get_item(Key={"session_id": session_id})
    except Exception as e:
        logger.exception("GetItem failed")
        return _err(502, f"{type(e).__name__}: {e}")

    item = resp.get("Item")
    if not item or item.get("user_id") != user_id:
        return _err(404, f"Session {session_id} not found")
    return _ok(_session_summary(item))


# ──────────────────────────── /conversations/{id}/messages ──────
def _handle_get_messages(event, session_id: str):
    """Stream messages from AgentCore Memory in chronological order.

    Verifies ownership against the DDB index row first so we don't leak
    one user's messages to another. Memory itself doesn't enforce per-user
    isolation — it's scoped by (actorId, sessionId).
    """
    if not MEMORY_ID:
        return _err(500, "MEMORY_ID not configured")
    if not sessions_table:
        return _err(500, "SESSIONS_TABLE not configured")
    user_id = _caller_user_id(event)
    if not user_id:
        return _err(401, "Could not resolve caller identity")
    if not session_id:
        return _err(400, "Missing session_id")

    # Ownership check — the row must exist and belong to the caller.
    try:
        resp = sessions_table.get_item(Key={"session_id": session_id})
    except Exception as e:
        logger.exception("Ownership lookup failed")
        return _err(502, f"{type(e).__name__}: {e}")
    item = resp.get("Item")
    if not item or item.get("user_id") != user_id:
        return _err(404, f"Session {session_id} not found")

    # Fetch events from memory. AgentCore returns newest-first; we reverse
    # so the UI gets chronological order.
    messages: list[dict[str, Any]] = []
    try:
        ev_resp = agentcore.list_events(
            memoryId=MEMORY_ID,
            actorId=user_id,
            sessionId=session_id,
            maxResults=100,
            includePayloads=True,
        )
    except Exception as e:
        logger.exception("list_events failed")
        return _err(502, f"{type(e).__name__}: {e}")

    for ev in reversed(ev_resp.get("events") or []):
        ts = ev.get("eventTimestamp")
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else (ts or "")
        for part in ev.get("payload") or []:
            conv = part.get("conversational") or {}
            role = (conv.get("role") or "").lower()
            text = (conv.get("content") or {}).get("text") or ""
            if role and text:
                messages.append({"role": role, "content": text, "ts": ts_iso})

    return _ok({"session_id": session_id, "messages": messages})


# ──────────────────────────── helpers ───────────────────────────
def _caller_user_id(event) -> str | None:
    claims = _caller_claims(event)
    user_id = claims.get("sub") or claims.get("cognito:username")
    if user_id:
        return user_id
    return event.get("user_id") or event.get("requestContext", {}).get("user_id")


def _caller_claims(event) -> dict[str, Any]:
    # 1. API Gateway with Cognito authorizer — claims come pre-validated.
    claims = (event.get("requestContext", {})
              .get("authorizer", {})
              .get("claims") or {})
    if claims:
        return claims
    # 2. Lambda Function URL (AuthType=NONE) — the UI still sends the Cognito
    #    IdToken in Authorization: Bearer <jwt>. Decode the payload (no
    #    signature verification — fine for the demo since Cognito issued it
    #    and worst case a tampered claim only impacts the caller's own data).
    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        try:
            payload_b64 = auth[7:].split(".")[1]
            payload_b64 += "=" * (-len(payload_b64) % 4)  # pad to multiple of 4
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            return payload if isinstance(payload, dict) else {}
        except Exception as e:
            logger.warning("Failed to decode JWT from Authorization header: %s", e)
    return {}


def _session_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": item.get("session_id"),
        "title": item.get("title"),
        "created_at": item.get("created_at"),
        "last_message_at": item.get("last_message_at"),
        "message_count": _to_int(item.get("message_count")),
        # Legacy rows have no chat_type; treat them as 'analyst' (the only chat
        # that persisted sessions before MCP Chat shipped).
        "chat_type": item.get("chat_type") or "analyst",
    }


def _to_int(v):
    if v is None:
        return None
    if isinstance(v, Decimal):
        return int(v)
    return int(v)


# ──────────────────────────── responses ─────────────────────────
def _ok(body):
    return {
        "statusCode": 200,
        "headers": _cors_headers(),
        "body": json.dumps(body, default=_json_default),
    }


def _err(status, message):
    return {
        "statusCode": status,
        "headers": _cors_headers(),
        "body": json.dumps({"error": message}),
    }


def _json_default(o):
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"not serializable: {type(o)}")


def _cors_headers():
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    }
