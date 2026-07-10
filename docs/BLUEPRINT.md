# Cloudera AI Process Mapper — Technical Blueprint v7

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
| **Discovery** | `GET /api/discovery` (auth passthrough) | Resolves platform token (§4.1), calls discovery service, returns a **capability matrix** (`mcp_servers[]`, `models[]`, `tools[]`) with `discovery_active` flag. On failure → **Sandbox Fallback Mode** (always HTTP 200). |
| **AI Seam** | `POST /agentic/suggest` | Consumes capability matrix; Phase 1 deterministic stub. Response echoes `discovery_active` for UI banner (§4.2). Phase 2: live Cloudera AI Inference call. |
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
- **`group`** (agentic underlay ↔ one Agent Studio *workflow*; SQLite table `"group"`) —
  `id`, `process_id`, `bbox_geometry`, `deployment_status` with DB-level CHECK constraint
  *(see §4.3.1)*, `workflow_definition_json?`, `agent_studio_workflow_id?`,
  `agent_studio_url?`, `inference_endpoint_url?`
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

The backend builds a **capability matrix** — the set of MCP servers, models, and tools the
suggest algorithm (5c) may reference. Discovery is attempted on every `GET /api/discovery`
(cache TTL ~5 min); the matrix is **never empty** after baseline merge (§4.1.3).

#### 4.1.1 Token source strategy (sequential)

The discovery client resolves credentials in this order:

1. **`CLOUDERA_AI_TOKEN`** — environment variable injected in the CML session / Application
   runtime context.
2. **`_cdswuserstoken` cookie** — extracted from the incoming HTTP request when the env var
   is absent (browser session proxied through the CML Application).

If neither source yields a token, discovery is treated as failed → Sandbox Fallback Mode
(§4.1.2). The resolved source is logged (never the token value).

#### 4.1.2 Sandbox Fallback Mode (5b → 5c soft degrade)

If `GET /api/discovery` cannot reach the platform (auth error, network timeout, missing
token, or gRPC/HTTP transport failure), the backend **does not return an error to the
client**. Instead it:

1. **Logs** the failure (reason + stack) server-side.
2. **Populates** the capability matrix with **baseline enterprise templates** (§4.1.3).
3. **Returns HTTP 200** with `"discovery_active": false`.

Step 5c **always proceeds** in Sandbox Fallback Mode — it is not blocked. The suggest
response echoes `discovery_active: false` so the UI can warn the operator.

#### 4.1.3 Baseline enterprise templates

Appended to any capability matrix when (a) discovery fails, or (b) a successful platform
response returns **empty arrays** (see §4.1.4). Baselines are merged without duplicates
(platform entries take precedence over name-colliding baselines).

| Category | Baseline entries |
|---|---|
| **models** | `llama-3-70b-instruct` |
| **tools** | `code_execution`, `vector_search` |
| **mcp_servers** | *(none — MCP list may remain empty in sandbox)* |

#### 4.1.4 Empty array policy

A **successful** platform discovery call returning HTTP/gRPC **200** with empty arrays
(e.g. `models: []`) is **not a failure**. It means the workspace has not registered custom
templates yet. The backend:

- Sets `"discovery_active": true` (platform was reached and authenticated).
- **Appends** baseline defaults (§4.1.3) so the matrix still gives 5c tools/models to reason with.
- Returns the merged matrix.

#### 4.1.5 Response contract

`GET /api/discovery` **always** returns HTTP 200 with:

```json
{
  "mcp_servers": [{ "id": "...", "name": "...", "url": "..." }],
  "models": [{ "id": "...", "name": "llama-3-70b-instruct", "provider": "..." }],
  "tools": [{ "id": "...", "name": "code_execution", "type": "builtin|custom|mcp" }],
  "discovery_active": true,
  "source": "platform"
}
```

Sandbox Fallback example:

```json
{
  "mcp_servers": [],
  "models": [{ "id": "baseline", "name": "llama-3-70b-instruct", "provider": "baseline" }],
  "tools": [
    { "id": "baseline", "name": "code_execution", "type": "builtin" },
    { "id": "baseline", "name": "vector_search", "type": "builtin" }
  ],
  "discovery_active": false,
  "source": "sandbox"
}
```

### 4.2 Sandbox Fallback UI (5b → 5c dependency)

When the frontend receives an optimization response (`POST /agentic/suggest` or a cached
group reload) where **`discovery_active` is `false`**, it displays a subtle amber banner
at the top of the canvas (dismissible per session):

> ⚠️ Demo Sandbox Mode: Cloudera Agent Studio discovery unreachable. Using baseline enterprise templates.

When `discovery_active` is `true`, no banner is shown.

### 4.3 Step 5c — State machine & Pydantic oracle

Step 5c is an **absolute verify-gate**: nothing from the inference/stub pipeline may touch
SQLite until it passes schema validation *and* capability-matrix subset checks.

#### 4.3.1 SQLite schema gate (`deployment_status` CHECK)

The `"group"` table (agentic underlay) enforces valid lifecycle states at the DB layer.
On schema init (SQLite has no `ALTER CONSTRAINT`; the CHECK is declared at `CREATE TABLE`):

```sql
CREATE TABLE IF NOT EXISTS "group" (
    ...
    deployment_status TEXT NOT NULL DEFAULT 'unlinked'
        CHECK (deployment_status IN (
            'unlinked', 'proposed', 'draft', 'linked', 'deployed'
        )),
    workflow_definition_json TEXT,
    ...
);
```

| Transition | Trigger |
|---|---|
| `unlinked` → `proposed` | `POST /agentic/suggest` passes oracle → persist blueprint |
| `proposed` → `draft` | User opens guided hand-off checklist |
| `draft` → `linked` | User pastes Agent Studio workflow URL |
| `linked` → `deployed` | User pastes inference endpoint URL |

Any `UPDATE` setting `deployment_status='proposed'` without a validated
`workflow_definition_json` is a **gate failure**.

#### 4.3.2 Pydantic oracle (`AgentStudioWorkflow`)

Implementation lives in `backend/app/schemas/agent_studio.py` (Pydantic v2). The inference
endpoint output must map to these models **before** persistence:

```python
class AgentStudioAgent(BaseModel):
    name: str = Field(..., description="Unique name of the agent inside this workflow")
    role: str
    goal: str
    backstory: str
    tools: list[str] = Field(default_factory=list)
    llm: str = "default"

class AgentStudioTask(BaseModel):
    description: str
    agent: str  # must match an AgentStudioAgent.name

class AgentStudioWorkflow(BaseModel):
    workflow_name: str
    type: Literal["task", "conversational"] = "task"
    manager_agent: bool = False
    planning: bool = False
    agents: list[AgentStudioAgent]  # min 1
    tasks: list[AgentStudioTask]    # min 1
    confidence: float               # 0.0–1.0
    rationale: str
```

**Cross-field validators (oracle rules):**

1. **Task→agent integrity:** every `tasks[].agent` must reference an existing `agents[].name`.
2. **Tool subset:** when `discovery_active=true`, every `agents[].tools[]` entry ⊆ merged
   capability-matrix tool names from the latest `GET /api/discovery`.
3. **LLM subset:** when `discovery_active=true`, every `agents[].llm` ∈ capability-matrix
   model names.
4. **Sandbox subset:** when `discovery_active=false`, tools ⊆ `{code_execution, vector_search}`
   (± baselines) and llm ∈ `{llama-3-70b-instruct, default}`.

Validation entry point: `validate_suggest_payload(raw, discovery_active, allowed_tools,
allowed_llms)` → returns `AgentStudioSuggestResponse` or raises before any DB write.

#### 4.3.3 Pre-persistence pipeline (5c)

```
POST /agentic/suggest { group_id }
  → load group (must exist; typically deployment_status='unlinked' or re-propose)
  → GET /api/discovery (cached) → capability matrix + discovery_active
  → call inference / run stub → raw dict
  → validate_suggest_payload(...)     ← HARD GATE (Pydantic oracle)
  → UPDATE "group" SET
       deployment_status = 'proposed',
       workflow_definition_json = <validated JSON>
  → return AgentStudioSuggestResponse
```

If validation fails → **HTTP 422** with field-level detail; `deployment_status` unchanged.

#### 4.3.4 API response envelope

`POST /agentic/suggest` returns `AgentStudioSuggestResponse` — workflow fields plus
`discovery_active` at the top level (triggers §4.2 banner when `false`):

| Field | Type | Required |
|---|---|---|
| `discovery_active` | bool | yes |
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
| `confidence` | float | yes (0.0–1.0) |
| `rationale` | string | yes |

Example (platform discovery active):

```json
{
  "discovery_active": true,
  "workflow_name": "Invoice Reconciliation",
  "type": "task",
  "manager_agent": false,
  "planning": false,
  "agents": [{
    "name": "PO Matcher",
    "role": "3-way match specialist",
    "goal": "Match invoice to PO and receipt, flag discrepancies",
    "backstory": "Read-only ERP access; validates, never approves.",
    "tools": ["code_execution", "vector_search"],
    "llm": "llama-3-70b-instruct"
  }],
  "tasks": [{ "description": "Validate invoice against PO", "agent": "PO Matcher" }],
  "confidence": 0.82,
  "rationale": "These sequential tasks share a single business object..."
}
```

### 4.4 Guided hand-off (no authoring API)

Agent Studio has no create/author API, so the dialog presents an **ordered, copy-ready checklist** of the exact steps and
suggested field values to recreate this workflow in Agent Studio. Opening the checklist
transitions the group to **`draft`** (user has reviewed the proposal):

1. Create workflow → set **name**, **type** (task/conversational), **manager-agent**/**planning** toggles.
2. For each agent → paste **role**, **goal**, **backstory**; attach **tools** (from the
   capability matrix: platform-discovered, baseline, or **MCP** when listed); pick **LLM**
   (from capability matrix).
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
   - **5b [Discovery Auth Passthrough]** `GET /api/discovery` → *verify:*
     - **Token resolution:** `CLOUDERA_AI_TOKEN` env, else `_cdswuserstoken` request cookie.
     - **Always HTTP 200** with capability matrix + `discovery_active` boolean.
     - **Platform success** (incl. empty arrays): `discovery_active=true`, baselines merged
       when arrays empty; matrix non-empty for models/tools.
     - **Sandbox Fallback** (auth/timeout/missing token): `discovery_active=false`,
       `source=sandbox`, baseline templates populated; error logged server-side.
   - **5c [Draft Optimization Generation]** "Agentic Options" on a group →
     `POST /agentic/suggest` → *verify:*
     - Pre-persistence oracle passes (`validate_suggest_payload` in `schemas/agent_studio.py`).
     - Invalid payload → HTTP 422; `deployment_status` unchanged.
     - Valid payload → `deployment_status='proposed'`; `workflow_definition_json` persisted.
     - `tasks[].agent` ∈ `agents[].name`; tools/llm ⊆ capability matrix per §4.3.2.
     - Response includes `discovery_active`; amber Sandbox banner when `false` (§4.2).
     - SQLite CHECK rejects any invalid `deployment_status` value.
   - **5d [Guided Hand-off]** Checklist + deep-link populated from proposed blueprint;
     user acknowledges → status `draft`; user pastes workflow URL → *verify:* opens in Studio;
     status → `linked`.
   - **5e [Endpoint Bind & Run]** Store endpoint URL + access key → status `deployed`;
     "Run" issues `kickoff` → *verify:* returns `trace_id`.
6. **[Data Lake Sync]** "Publish" → *verify:* flattened state in Iceberg, readable from CDW/Spark; labor-hours query returns a number.

**Stretch:** XPDL parser · live Cloudera AI Inference `/agentic/suggest` · **hard-block 5c**
when `discovery_active=false` (production mode; demo uses soft degrade per §4.1.2) ·
process-intelligence suggestions (Narrative 2) · round-trip BPMN export · auto-discover
deployed endpoints instead of manual paste.

## Appendix — Key References

- Cloudera Agent Studio (agent fields: Name, Role, Goal, Backstory, Tools; workflow-level: type, manager agent, planning; "Generate with AI" authoring): <https://docs.cloudera.com/machine-learning/cloud/use-ai-studios/topics/ml-create-workflow.html>
- Deploying workflows as model endpoints: <https://docs.cloudera.com/machine-learning/cloud/use-ai-studios/topics/ml-deploy-workflow-model-endpoint.html>
- Supported model-endpoint API operations (kickoff, get configuration): <https://docs.cloudera.com/machine-learning/1.5.5/use-ai-studios/topics/ml-supported-api-operations.html>
- Cloudera AI Inference service REST API: <https://docs.cloudera.com/machine-learning/cloud/rest-api-reference-ai-inference-service/index.html>
- Agent Studio repo (`CAI_STUDIO_AGENT`): <https://github.com/cloudera/CAI_STUDIO_AGENT>

> **Confirmed constraint:** Cloudera Agent Studio does not currently expose an API to
> create/author a workflow programmatically. Phase 1 therefore uses a guided setup dialog
> (suggested values + deep-link) with manual link-back of the workflow URL and inference endpoint.
