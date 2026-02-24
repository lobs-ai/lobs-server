# workflow_tool.py

Scriptable workflow CRUD for Lobs/agents.

## Commands

- List workflows:
  - `python3 bin/workflow_tool.py list`
- Show one workflow JSON:
  - `python3 bin/workflow_tool.py get --id <workflow-id>`
- Export editable JSON payload:
  - `python3 bin/workflow_tool.py export --id <workflow-id> --out workflow.json`
- Apply JSON (upsert by name):
  - `python3 bin/workflow_tool.py apply --file workflow.json`
- Apply JSON to specific workflow id:
  - `python3 bin/workflow_tool.py apply --file workflow.json --id <workflow-id>`

## Auth

The tool tries in this order:
1. `--token`
2. `LOBS_API_TOKEN` / `MISSION_CONTROL_TOKEN`
3. newest active token from `data/lobs.db`

Override API base with `--base http://host:8000`.
