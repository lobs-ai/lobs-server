# Agent Scripts Reference

All agents have access to these scripts in `./scripts/`:

## Task Management — `./scripts/lobs-tasks`
```bash
./scripts/lobs-tasks list                             # List all active tasks
./scripts/lobs-tasks list-mine                        # List tasks assigned to me
./scripts/lobs-tasks get <task-id>                    # Get task details
./scripts/lobs-tasks create "title" --project <id> --agent <type> --notes "text"
./scripts/lobs-tasks set-agent <task-id> <agent>      # Assign agent to task
./scripts/lobs-tasks complete <task-id>               # Mark task completed
./scripts/lobs-tasks fail <task-id> [reason]          # Mark task failed
./scripts/lobs-tasks block <task-id> [reason]         # Mark task blocked
./scripts/lobs-tasks set-status <task-id> <status>    # active/completed/todo
./scripts/lobs-tasks set-work-state <task-id> <state> # not_started/ready/in_progress/completed/failed/blocked
```

## System Status — `./scripts/lobs-status`
```bash
./scripts/lobs-status overview       # Full system overview
./scripts/lobs-status agents         # Agent statuses + stats
./scripts/lobs-status activity       # Recent activity feed
./scripts/lobs-status orchestrator   # Orchestrator status
./scripts/lobs-status projects       # List projects
```

## Inbox — `./scripts/lobs-inbox`
```bash
./scripts/lobs-inbox list                                          # List inbox items
./scripts/lobs-inbox get <id>                                      # Get inbox details
./scripts/lobs-inbox create "title" --content "text" --severity medium
```

## Available Agents
| Agent | Role | Best For |
|-------|------|----------|
| programmer | Code implementation | Writing code, fixing bugs, refactoring |
| researcher | Investigation | Research, analysis, comparison |
| architect | System design | Technical strategy, design docs |
| reviewer | Quality assurance | Code review, quality checks |
| writer | Documentation | Docs, summaries, content |

## Projects
| ID | Description |
|----|-------------|
| lobs-server | FastAPI backend + orchestrator |
| lobs-mission-control | macOS SwiftUI command center |
| lobs-mobile | iOS SwiftUI companion app |
| self-improvement | Meta-improvements to the agent system |
