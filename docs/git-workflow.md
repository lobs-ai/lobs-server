# Git Workflow

**Last Updated:** 2026-02-20

Branch strategy, commit conventions, and collaboration patterns for lobs-server development.

---

## Branch Strategy

### Main Branch

**Branch:** `main`  
**Protection:** None (small team, high trust)  
**Deployment:** Manual (currently no CI/CD)

**Philosophy:**
- Work directly on `main` for most changes
- Small, frequent commits preferred over large batches
- Trust + async collaboration > rigid process

### When to Use Feature Branches

Use a feature branch for:
- **Large refactors** - Multi-file changes that touch core systems
- **Experimental work** - Trying something that might not pan out
- **Breaking changes** - Need to coordinate with other developers
- **Long-running work** - Takes multiple days and might conflict with others

**Naming:** `feature/short-description` or `fix/issue-description`

```bash
git checkout -b feature/provider-health-tracking
# ... work work work ...
git push origin feature/provider-health-tracking
# Merge via PR or direct merge:
git checkout main
git merge feature/provider-health-tracking
git push origin main
git branch -d feature/provider-health-tracking
```

### Agent Workflow

AI agents typically:
- Work on `main` for small, focused changes
- Create feature branches for large or risky work
- Auto-commit with descriptive messages
- Auto-push after completion (orchestrator handles this)

---

## Commit Conventions

### Format

Use [Conventional Commits](https://www.conventionalcommits.org/) format:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Examples:**
```bash
feat: add provider health tracking
fix: prevent N+1 query in activity endpoint
docs: update ARCHITECTURE.md with orchestrator flow
refactor: extract model chooser into separate module
test: add coverage for WebSocket reconnection
chore: update dependencies
```

### Types

| Type | Use For |
|------|---------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `docs` | Documentation only (no code changes) |
| `refactor` | Code restructuring (no behavior change) |
| `test` | Adding or updating tests |
| `perf` | Performance improvement |
| `chore` | Build, config, dependencies, tooling |
| `style` | Code formatting (no logic change) |
| `ci` | CI/CD pipeline changes |

### Scope (Optional)

Add scope for context:
- `feat(orchestrator): add health-aware model routing`
- `fix(api): return 404 for missing tasks`
- `test(websocket): add reconnection tests`

### Description

- Use imperative mood: "add feature" not "added feature"
- Start with lowercase
- No period at the end
- Keep under 72 characters

### Body (Optional)

Add context for non-obvious changes:
- Why this change was needed
- What alternatives were considered
- What side effects to watch for

```bash
git commit -m "refactor: extract model chooser into separate module

The model selection logic was embedded in worker.py and hard to test.
Extracting it into model_chooser.py makes it easier to unit test
and reuse across different worker types.

No behavior change - just reorganization."
```

### Footer (Optional)

Reference issues or breaking changes:
```bash
git commit -m "feat: add provider health API endpoints

BREAKING CHANGE: /api/orchestrator/models endpoint removed,
replaced with /api/orchestrator/providers/health"
```

---

## Commit Frequency

### Prefer Small Commits

**Good:**
```bash
git commit -m "feat: add provider health tracking data model"
git commit -m "feat: add provider health API endpoints"
git commit -m "test: add provider health unit tests"
git commit -m "docs: document provider health system"
```

**Avoid:**
```bash
git commit -m "feat: add provider health tracking, API, tests, and docs"
# (hard to review, hard to revert, loses granularity)
```

### When to Commit

- After each logical unit of work
- Before switching tasks
- Before lunch/end of day (don't lose work)
- After tests pass

### WIP Commits

Avoid "WIP" commits on `main`. If you need to save work:
```bash
# Use a feature branch
git checkout -b wip/experiment
git commit -m "wip: trying new approach"

# Or use stash
git stash save "half-done feature"
```

---

## Collaboration

### Pull Requests (Optional)

PRs are **optional** for this project. Use them when:
- You want human review before merge
- Making breaking changes
- Working on complex/risky features

**PR Guidelines:**
- **Title:** Same format as commit message
- **Description:** What changed, why, how to test
- **Review:** Tag reviewers if needed, otherwise self-merge
- **Merge:** Use "Squash and merge" or "Merge commit" (no rebase)

### Code Review

When reviewing (human or AI):
- Check the [Code Review Checklist](coding-standards.md#code-review-checklist)
- Run tests locally
- Test the feature manually if applicable
- Leave comments for questions/suggestions
- Approve when ready

### Resolving Conflicts

When conflicts arise:
```bash
git pull origin main
# Fix conflicts in editor
git add .
git commit -m "merge: resolve conflicts with main"
git push origin main
```

---

## Agent-Specific Patterns

### Orchestrator Auto-Push

The orchestrator automatically commits and pushes worker changes:

```python
# In app/orchestrator/engine.py
# After worker completes task
if repo_changed:
    subprocess.run(["git", "add", "."], cwd=project_path)
    subprocess.run(
        ["git", "commit", "-m", f"task({task.id}): {summary}"],
        cwd=project_path
    )
    subprocess.run(["git", "push", "origin", "main"], cwd=project_path)
```

### Task-Based Commits

Worker agents commit with task ID in message:
```bash
git commit -m "task(5A3B9C1D): add WebSocket reconnection logic"
```

**Format:** `task(<task_id>): <description>`

**Benefits:**
- Easy to trace commits back to tasks
- Links work to project management
- Searchable commit history

### Daily Compression

The orchestrator may batch-commit housekeeping changes:
```bash
git commit -m "chore: daily compression - archive completed tasks"
```

---

## History Management

### Rebasing (Generally Avoid)

- Don't rebase `main` (creates confusion for distributed team)
- Don't rewrite public history
- Rebasing is OK for local feature branches before merging

### Reverting

To undo a commit:
```bash
# Safe - creates new commit that undoes changes
git revert <commit-hash>
git push origin main
```

**Don't use:** `git reset --hard` on `main` (loses history)

### Squashing (Optional)

Squash commits when merging feature branches:
```bash
git checkout main
git merge --squash feature/my-feature
git commit -m "feat: my feature summary"
git push origin main
```

---

## Tags & Releases

### Versioning

Not currently used, but consider for future:
- Use semantic versioning: `v1.2.3`
- Tag major milestones
- Generate changelog from commits

```bash
git tag -a v1.0.0 -m "Release 1.0.0 - Provider health tracking"
git push origin v1.0.0
```

---

## Best Practices

### DO
- ✅ Commit early, commit often
- ✅ Write descriptive commit messages
- ✅ Test before committing
- ✅ Push frequently (don't hoard local commits)
- ✅ Pull before starting new work

### DON'T
- ❌ Commit broken code to `main`
- ❌ Push untested changes
- ❌ Use generic messages ("fix", "update", "wip")
- ❌ Commit secrets, tokens, or credentials
- ❌ Rewrite public history

---

## Tooling

### Pre-commit Hooks (Future)

Consider adding:
- Run tests before commit
- Check for secrets
- Format code with `black` or `ruff`
- Type check with `mypy`

**Setup:**
```bash
pip install pre-commit
pre-commit install
```

### Git Aliases (Optional)

Add to `~/.gitconfig`:
```ini
[alias]
    st = status
    co = checkout
    br = branch
    cm = commit -m
    aa = add .
    lg = log --oneline --graph --decorate
```

---

## Recovery

### Lost Commits

If you accidentally lose commits:
```bash
# View reflog
git reflog

# Recover commit
git checkout <commit-hash>
git checkout -b recovery-branch
```

### Accidental Push

If you push broken code:
```bash
# Revert the commit (creates new commit)
git revert <bad-commit-hash>
git push origin main

# Or create a fix immediately
git commit -m "fix: correct issue from previous commit"
git push origin main
```

---

## See Also

- **[coding-standards.md](coding-standards.md)** - Code quality and review standards
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - General contribution guide
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** - System architecture overview
