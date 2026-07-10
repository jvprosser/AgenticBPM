# Cloudera AI Process Mapper — Technical Blueprint v5

## 0. Purpose & Demo Narrative

A business-process mapping tool that ingests real BPMN, lets a user visually **wrap a
subprocess in a proposed Cloudera Agent Studio workflow**, and quantifies the payoff.
The closing insight — Cloudera Data Warehouse over Iceberg — answers:

> **"How many labor-hours across our processes are covered by proposed agents?"**

Every design choice serves that north-star metric.

## 1. Architecture

| Layer | Choice | Notes |
|---|---|---|
| **Frontend** | React + **React Flow** (`@xyflow/react`) | Server-authoritative; no client-side domain logic. Locked topology, drag-to-reposition, agentic bounding-box groups, metadata popovers, agentic-options dialog. |
| **Backend** | **FastAPI** on a Cloudera AI (CML) Application | Owns BPMN parsing, graph model, metadata, discovery proxy, agentic suggest. Pydantic = typed contract; free OpenAPI docs. |
| **Operational DB** | **SQLite** in CML project storage | **Single-replica, WAL mode, `busy_timeout`.** Single-user demo scope (NFS-locking boundary documented). |
| **Discovery** | `GET /api/discovery` (auth passthrough) | Proxies the platform discovery service using the active CML/CDP token from env; returns parsed `mcp_servers[]`, `models[]`, `tools[]`. |
| **AI Seam** | `POST /agentic/suggest` | Phase 1: deterministic stub shaped like Agent Studio "Generate with AI"; consumes discovery results when available. Phase 2: live Cloudera AI Inference call. |
| **Analytics Sync** | Async **"Publish"** → **Iceberg** via **PyIceberg** | Queryable in **CDW (Impala/Hive) or Spark** — CDP-native (not Trino). |

**Guardrails from day 0:** single replica · `PRAGMA journal_mode=WAL` + `busy_timeout=5000`
· writes serialized through one FastAPI process · drag saves on **drag-end, debounced ~250ms**.

## 2. Locked vs. Mutable (confirmed)

| Element | State |
|---|---|
| Nodes (existence + BPMN type) | **Locked** |
| Edges (process flow) | **Locked** |
| Pools / lanes | **Locked**, rendered as read-only containers |
| Node X/Y position | Mutable |
| Group membership + bbox geometry | Mutable |
| Node/group metadata | Mutable |

A group **MAY span lane boundaries** (deliberate demo highlight: an agent automating
cross-department work).

## 3. Data Model (SQLite)

- **`process`** — `id`, `filename`, `format='bpmn'`, `raw_xml`, `created_at`
- **`node`** — `id`, `process_id`, `source_ref`, `type`, `label`, `x`, `y`, `lane_id?`, `group_id?`, `parent_ref?` (containing subprocess), `attached_to_ref?` (host node for boundary events)  *(`group_id` nullable FK ⇒ many nodes → one group; `lane_id` nullable — some nodes, e.g. `Gateway_MergePayout`, sit outside any lane)*
- **`edge`** — `id`, `process_id`, `source_node_id`, `target_node_id` *(read-only)*
- **`lane`** — `id`, `process_id`, `label` *(read-only container)*
- **`group`** (agentic underlay ↔ one Agent Studio *workflow*) — `id`, `process_id`, `bbox_geometry`, `deployment_status` (`unlinked`|`proposed`|`draft`|`linked`|`deployed`), `workflow_definition_json?`, `agent_studio_workflow_id?`, `agent_studio_url?`, `inference_endpoint_url?`
- **`metadata`** — `owner_type` (`node`|`group`), `owner_id`, `name`, `owner`, `duration_value` (int), `duration_unit` (`minutes`|`hours`|`days`), `description`

**Mandatory:** `duration_value`/`duration_unit` on **every node** — the automatable-labor-hours
rollup depends on it.

## 4. The Agentic Seam — Cloudera Agent Studio (guided hand-off)

A `group` links to a **Cloudera Agent Studio workflow** (which contains ≥1 agents).
"Agentic Options" is a five-state dialog:

| State | Meaning | Stored |
|---|---|---|
| **Unlinked** | Group exists; no workflow work started | — |
| **Proposed** | Suggestion algorithm ran; blueprint cached, not yet user-reviewed | `workflow_definition_json`, `deployment_status='proposed'` |
| **Draft** | User reviewed/edited the guided hand-off checklist | `workflow_definition_json` (possibly edited) |
| **Linked** | Workflow created in Studio (manually) | `agent_studio_workflow_id`, `agent_studio_url` |
| **Deployed** | Deployed to Cloudera AI Inference | `inference_endpoint_url` (+ access-key ref) |

State flow: `unlinked` → `proposed` → `draft` → `linked` → `deployed`.

### 4.1 Platform discovery (Step 5b)

Before generating a workflow blueprint, the backend calls the **platform discovery service**
with the active token injected by the CML/CDP runtime (e.g. `CDP_TOKEN`, `CDSW_APIV2_KEY` —
exact env var is environment-specific and documented at implementation time).

`GET /api/discovery` returns a normalized payload:

```json
{
  "mcp_servers": [{ "id": "...", "name": "...", "url": "..." }],
  "models": [{ "id": "...", "name": "...", "provider": "..." }],
  "tools": [{ "id": "...", "name": "...", "type": "builtin|custom|mcp" }],
  "discovery_ok": true,
  "source": "platform"
}
```

**Verify gate policy:** HTTP 200 + parsed arrays (arrays may be **empty** — that is not a
failure). Auth or transport failure sets `discovery_ok: false`.

**Degrade policy (demo):** If discovery fails, Step 5c **soft-degrades** — the suggest
algorithm runs with built-in default tool/model names and the UI shows a visible banner
(`Discovery unavailable; using defaults`). This keeps the demo runnable in environments
without a discovery endpoint. Production deployments may choose to hard-block 5c instead.

Discovery results are cached in-memory (session TTL ~5 min) to avoid hammering the platform
on every "Agentic Options" click.

### 4.2 Workflow blueprint contract (Pydantic `AgentStudioWorkflow`)

`POST /agentic/suggest` must return a body that validates against this schema — the
**verify oracle** for Step 5c:

| Field | Type | Required |
|---|---|---|
| `workflow_name` | string | yes |
| `type` | `task` \| `conversational` | yes |
| `manager_agent` | bool | yes |
| `planning` | bool | yes |
| `agents[]` | array | yes (≥1) |
| `agents[].name` | string | yes |
| `agents[].role` | string | yes |
| `agents[].goal` | string | yes |
| `agents[].backstory` | string | yes |
| `agents[].tools` | string[] | yes |
| `agents[].llm` | string | yes |
| `tasks[]` | array | yes (≥1) |
| `tasks[].description` | string | yes |
| `tasks[].agent` | string | yes (must match an agent name) |
| `confidence` | float | yes |
| `rationale` | string | yes |

When `discovery_ok=true`, every entry in `agents[].tools` and `agents[].llm` should
reference a discovered tool/model name (subset check in verify gate).

**Proposed — generate the definition.** `POST /agentic/suggest` (stubbed in Phase 1) returns
an Agent-Studio-shaped workflow — identical to what Studio's own **"Generate with AI"**
produces. On success the group's `deployment_status` becomes **`proposed`** and the JSON is
cached in `workflow_definition_json`:

```json
{
  "workflow_name": "Invoice Reconciliation",
  "type": "task",
  "manager_agent": false,
  "planning": false,
  "agents": [{
    "name": "PO Matcher",
    "role": "3-way match specialist",
    "goal": "Match invoice to PO and receipt, flag discrepancies",
    "backstory": "Read-only ERP access; validates, never approves.",
    "tools": ["po_lookup", "code_execution"],
    "llm": "default"
  }],
  "tasks": [{ "description": "Validate invoice against PO", "agent": "PO Matcher" }],
  "confidence": 0.82,
  "rationale": "These sequential tasks share a single business object..."
}
```

### 4.3 Guided hand-off (no authoring API)

Agent Studio has no create/author API, so the dialog presents an **ordered, copy-ready checklist** of the exact steps and
suggested field values to recreate this workflow in Agent Studio. Opening the checklist
transitions the group to **`draft`** (user has reviewed the proposal):

1. Create workflow → set **name**, **type** (task/conversational), **manager-agent**/**planning** toggles.
2. For each agent → paste **role**, **goal**, **backstory**; attach **tools** (from discovery
   results when available: built-in *Artifact Files*/*Code Execution*, custom, or **MCP**);
   pick **LLM** (from discovery when available).
3. Define **tasks** and assign to agents.
4. A **deep-link** button opens Agent Studio's create-workflow page.

**Link-back (manual).** After creating & deploying in Studio, the user pastes the
**workflow URL** (→ `linked`) and the **model endpoint URL + access key** (→ `deployed`)
back into the dialog. Endpoint `Get Configuration` can later reconcile stored vs. live definition.

**Run.** When `deployed`, a "Run" affordance issues the documented async kickoff:
`POST { "request": { "action_type": "kickoff", "kickoff_inputs": <base64 JSON> } }`
with a CDP bearer token → returns a `trace_id` to display.

## 5. Analytics Target (Publish → Iceberg)

One wide, one-row-per-node table — the CDW query artifact:

| process_id | node_id | node_type | label | lane_id | group_id | agent_workflow_name | deployment_status | owner | duration_value | duration_unit |
|---|---|---|---|---|---|---|---|---|---|---|

Closing query: `SUM(duration_value) WHERE group_id IS NOT NULL`
→ **labor-hours in agentic groups** (any status).

Refined payoff query: `… AND deployment_status IN ('proposed','draft','linked','deployed')`
→ **labor-hours covered by proposed agents**; add `AND deployment_status='deployed'` for
**deployed-only** coverage.

## 6. Goal-Driven Execution Plan (each step gated by a verify)

0. **[Scaffold]** FastAPI + React deploy as a CML Application → *verify:* `/health` = 200 at CML app URL.
1. **[Upload]** React dropzone posts raw XML → *verify:* 200, file received.
2. **[Backend Ingestion]** Parse **BPMN 2.0** (reference test file: `docs/Claims_process.xml`) → extract semantic nodes, edges, lanes, and nested subprocess children.
   - **Layout resolution:** check for **Diagram Interchange** (`bpmndi:BPMNDiagram`) coordinates and use them when present.
   - **Fallback (required — `Claims_process.xml` ships *no* DI section):** apply a deterministic **"dumb cascade"** staggered X/Y assignment by walking the sequence flows (a layered/topological auto-layout is a stretch refinement).
   - **Element coverage (per the reference file):** events (`startEvent`, `endEvent`, `boundaryEvent` + timer), tasks (`serviceTask`, `userTask`, `businessRuleTask`), gateways (`exclusiveGateway`, `parallelGateway`), `subProcess` with nested children, plus `collaboration`/`participant` (pool) and `laneSet`/`lane`.
   - **Namespace caveat:** `Claims_process.xml` uses placeholder namespace URIs (`http://omg.org` for `bpmn`, `bpmndi`, `dc`, `di`) rather than the real OMG BPMN 2.0 URIs. A strict namespace-aware parser will choke on this — the parser must match on **local element names** (or tolerate arbitrary namespace URIs) instead of hard-coding the canonical OMG namespaces.
   - Persist to SQLite incl. `raw_xml` provenance → *verify:* DB contains nodes with **distinct X/Y coordinates** ready for the React canvas.
3. **[Constrained Editor]** Render canvas from SQLite; drag-and-drop → *verify:* drag-**end** triggers debounced X/Y write.
4. **[Agentic Underlay]** Bounding-box grouping → *verify:* selected nodes get one `group_id`; group row created (may cross lanes). ✅ *implemented*
5. **[Metadata & Agentic Seam]** — locked behind explicit sub-gates (must pass in order):
   - **5a [Metadata Persistence]** ✅ *implemented* — metadata popover on node **and** group;
     PATCH persists to SQLite; survives page refresh.
   - **5b [Discovery Auth Passthrough]** `GET /api/discovery` with active platform token
     from env → *verify:* HTTP 200, response parses to `{ mcp_servers[], models[], tools[] }`
     (arrays may be empty; auth/transport failure = gate fail). On failure, soft-degrade path
     documented in §4.1.
   - **5c [Draft Optimization Generation]** "Agentic Options" on a group →
     `POST /agentic/suggest` → *verify:* `deployment_status='proposed'`; response body
     validates against `AgentStudioWorkflow` Pydantic schema; `workflow_definition_json`
     persisted on group; when discovery succeeded, `agents[].tools` ⊆ discovered tools.
   - **5d [Guided Hand-off]** Checklist + deep-link populated from proposed blueprint;
     user acknowledges → status `draft`; user pastes workflow URL → *verify:* opens in Studio;
     status → `linked`.
   - **5e [Endpoint Bind & Run]** Store endpoint URL + access key → status `deployed`;
     "Run" issues `kickoff` → *verify:* returns `trace_id`.
6. **[Data Lake Sync]** "Publish" → *verify:* flattened state in Iceberg, readable from CDW/Spark; labor-hours query returns a number.

**Stretch:** XPDL parser · live Cloudera AI Inference `/agentic/suggest` · hard-block 5c when
discovery fails (production mode) · process-intelligence suggestions (Narrative 2) ·
round-trip BPMN export · auto-discover deployed endpoints instead of manual paste.

## Appendix — Key References

- Cloudera Agent Studio (agent fields: Name, Role, Goal, Backstory, Tools; workflow-level: type, manager agent, planning; "Generate with AI" authoring): <https://docs.cloudera.com/machine-learning/cloud/use-ai-studios/topics/ml-create-workflow.html>
- Deploying workflows as model endpoints: <https://docs.cloudera.com/machine-learning/cloud/use-ai-studios/topics/ml-deploy-workflow-model-endpoint.html>
- Supported model-endpoint API operations (kickoff, get configuration): <https://docs.cloudera.com/machine-learning/1.5.5/use-ai-studios/topics/ml-supported-api-operations.html>
- Cloudera AI Inference service REST API: <https://docs.cloudera.com/machine-learning/cloud/rest-api-reference-ai-inference-service/index.html>
- Agent Studio repo (`CAI_STUDIO_AGENT`): <https://github.com/cloudera/CAI_STUDIO_AGENT>

> **Confirmed constraint:** Cloudera Agent Studio does not currently expose an API to
> create/author a workflow programmatically. Phase 1 therefore uses a guided setup dialog
> (suggested values + deep-link) with manual link-back of the workflow URL and inference endpoint.
