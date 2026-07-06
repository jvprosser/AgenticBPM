# Cloudera AI Process Mapper — Technical Blueprint v4

## 0. Purpose & Demo Narrative

A business-process mapping tool that ingests real BPMN, lets a user visually **wrap a
subprocess in a proposed Cloudera Agent Studio workflow**, and quantifies the payoff.
The closing insight — Cloudera Data Warehouse over Iceberg — answers:

> **"How many labor-hours across our processes are covered by proposed agents?"**

Every design choice serves that north-star metric.

## 1. Architecture

| Layer | Choice | Notes |
|---|---|---|
| **Frontend** | React + **React Flow** (`@xyflow/react`) | Server-authoritative; no client-side domain logic. Locked topology, drag-to-reposition, agentic bounding-box groups, context menus, typed metadata forms. |
| **Backend** | **FastAPI** on a Cloudera AI (CML) Application | Owns BPMN parsing, graph model, all endpoints. Pydantic = typed contract; free OpenAPI docs. |
| **Operational DB** | **SQLite** in CML project storage | **Single-replica, WAL mode, `busy_timeout`.** Single-user demo scope (NFS-locking boundary documented). |
| **AI Seam** | `POST /agentic/suggest` | **Stubbed** in Phase 1; returns the Agent-Studio-shaped workflow definition (§4). Phase 2 swaps for a Cloudera AI Inference call. |
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
- **`group`** (agentic underlay ↔ one Agent Studio *workflow*) — `id`, `process_id`, `bbox_geometry`, `deployment_status` (`unlinked`|`draft`|`linked`|`deployed`), `workflow_definition_json?`, `agent_studio_workflow_id?`, `agent_studio_url?`, `inference_endpoint_url?`
- **`metadata`** — `owner_type` (`node`|`group`), `owner_id`, `name`, `owner`, `duration_value` (int), `duration_unit` (`minutes`|`hours`|`days`), `description`

**Mandatory:** `duration_value`/`duration_unit` on **every node** — the automatable-labor-hours
rollup depends on it.

## 4. The Agentic Seam — Cloudera Agent Studio (guided hand-off)

A `group` links to a **Cloudera Agent Studio workflow** (which contains ≥1 agents).
"Agentic Options" is a four-state dialog:

| State | Meaning | Stored |
|---|---|---|
| **Unlinked** | No workflow yet | — |
| **Draft** | Definition authored in-tool | `workflow_definition_json` |
| **Linked** | Workflow created in Studio (manually) | `agent_studio_workflow_id`, `agent_studio_url` |
| **Deployed** | Deployed to Cloudera AI Inference | `inference_endpoint_url` (+ access-key ref) |

**Draft — author the definition.** `POST /agentic/suggest` (stubbed) returns an
Agent-Studio-shaped workflow — identical to what Studio's own **"Generate with AI"** produces:

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

**Hand-off — guided setup dialog (no authoring API).** Agent Studio has no create/author
API, so the dialog presents an **ordered, copy-ready checklist** of the exact steps and
suggested field values to recreate this workflow in Agent Studio:

1. Create workflow → set **name**, **type** (task/conversational), **manager-agent**/**planning** toggles.
2. For each agent → paste **role**, **goal**, **backstory**; attach **tools** (built-in *Artifact Files*/*Code Execution*, custom, or **MCP**); pick **LLM**.
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
(optionally `AND deployment_status='deployed'`) → **labor-hours covered by
proposed/deployed agents.**

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
4. **[Agentic Underlay]** Bounding-box grouping → *verify:* selected nodes get one `group_id`; group row created (may cross lanes).
5. **[Metadata & Draft]** Context menus → metadata dialog (Name, Owner, Duration value+unit, Description); "Agentic Options" calls `/agentic/suggest`, caches definition → *verify:* `deployment_status='draft'`, definition persisted.
   - **5b [Guided Hand-off]** Checklist + deep-link; user pastes workflow URL → *verify:* opens in Studio; status → `linked`.
   - **5c [Endpoint Bind & Run]** Store endpoint URL; "Run" issues `kickoff` → *verify:* status → `deployed`; kickoff returns `trace_id`.
6. **[Data Lake Sync]** "Publish" → *verify:* flattened state in Iceberg, readable from CDW/Spark; labor-hours query returns a number.

**Stretch:** XPDL parser · live Cloudera AI Inference `/agentic/suggest` · process-intelligence
suggestions (Narrative 2) · round-trip BPMN export · auto-discover deployed endpoints instead
of manual paste.

## Appendix — Key References

- Cloudera Agent Studio (agent fields: Name, Role, Goal, Backstory, Tools; workflow-level: type, manager agent, planning; "Generate with AI" authoring): <https://docs.cloudera.com/machine-learning/cloud/use-ai-studios/topics/ml-create-workflow.html>
- Deploying workflows as model endpoints: <https://docs.cloudera.com/machine-learning/cloud/use-ai-studios/topics/ml-deploy-workflow-model-endpoint.html>
- Supported model-endpoint API operations (kickoff, get configuration): <https://docs.cloudera.com/machine-learning/1.5.5/use-ai-studios/topics/ml-supported-api-operations.html>
- Cloudera AI Inference service REST API: <https://docs.cloudera.com/machine-learning/cloud/rest-api-reference-ai-inference-service/index.html>
- Agent Studio repo (`CAI_STUDIO_AGENT`): <https://github.com/cloudera/CAI_STUDIO_AGENT>

> **Confirmed constraint:** Cloudera Agent Studio does not currently expose an API to
> create/author a workflow programmatically. Phase 1 therefore uses a guided setup dialog
> (suggested values + deep-link) with manual link-back of the workflow URL and inference endpoint.
