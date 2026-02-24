# Plugin System Implementation Guide

**Quick Start for Programmers**

This guide walks you through implementing the plugin system designed in [plugin-system-ADR.md](plugin-system-ADR.md).

---

## Phase 1: Core Infrastructure (Est. 3-4 days)

### Step 1: Database Schema (30 min)

Create migration file:

**File:** `migrations/012_add_plugin_tables.sql`
```sql
-- plugins table
CREATE TABLE plugins (
    plugin_id TEXT PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    version TEXT NOT NULL,
    description TEXT,
    author TEXT,
    manifest_json TEXT NOT NULL,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_plugins_enabled ON plugins(enabled);

-- plugin_permissions table
CREATE TABLE plugin_permissions (
    permission_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    permission_type TEXT NOT NULL,
    resource TEXT NOT NULL,
    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id),
    UNIQUE(plugin_id, permission_type, resource)
);

CREATE INDEX idx_plugin_permissions_plugin ON plugin_permissions(plugin_id);

-- plugin_executions table
CREATE TABLE plugin_executions (
    execution_id TEXT PRIMARY KEY,
    plugin_id TEXT NOT NULL,
    task_id TEXT,
    agent_type TEXT,
    input_json TEXT,
    output_json TEXT,
    error_message TEXT,
    exit_code INTEGER,
    duration_ms INTEGER,
    executed_at TEXT NOT NULL,
    FOREIGN KEY (plugin_id) REFERENCES plugins(plugin_id),
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

CREATE INDEX idx_plugin_executions_plugin ON plugin_executions(plugin_id);
CREATE INDEX idx_plugin_executions_task ON plugin_executions(task_id);
CREATE INDEX idx_plugin_executions_agent ON plugin_executions(agent_type);
```

Run migration:
```bash
sqlite3 lobs.db < migrations/012_add_plugin_tables.sql
```

### Step 2: Data Models (1 hour)

**File:** `app/models.py` (add to existing file)
```python
from sqlalchemy import Column, String, Boolean, Integer, Text, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base
import uuid
from datetime import datetime

class Plugin(Base):
    __tablename__ = "plugins"
    
    plugin_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, unique=True, nullable=False)
    version = Column(String, nullable=False)
    description = Column(Text)
    author = Column(String)
    manifest_json = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    updated_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    
    permissions = relationship("PluginPermission", back_populates="plugin", cascade="all, delete-orphan")
    executions = relationship("PluginExecution", back_populates="plugin")

class PluginPermission(Base):
    __tablename__ = "plugin_permissions"
    
    permission_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plugin_id = Column(String, ForeignKey("plugins.plugin_id"), nullable=False)
    permission_type = Column(String, nullable=False)  # 'network', 'filesystem_read', etc.
    resource = Column(String, nullable=False)
    
    plugin = relationship("Plugin", back_populates="permissions")

class PluginExecution(Base):
    __tablename__ = "plugin_executions"
    
    execution_id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    plugin_id = Column(String, ForeignKey("plugins.plugin_id"), nullable=False)
    task_id = Column(String, ForeignKey("tasks.task_id"))
    agent_type = Column(String)
    input_json = Column(Text)
    output_json = Column(Text)
    error_message = Column(Text)
    exit_code = Column(Integer)
    duration_ms = Column(Integer)
    executed_at = Column(String, default=lambda: datetime.utcnow().isoformat())
    
    plugin = relationship("Plugin", back_populates="executions")
```

**File:** `app/schemas.py` (add to existing file)
```python
from pydantic import BaseModel
from typing import List, Optional, Dict, Any

class PluginManifest(BaseModel):
    name: str
    version: str
    description: str
    agent_types: List[str]
    author: str
    executable: str
    runtime: str
    timeout_seconds: int = 30
    permissions: Dict[str, List[str]]
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    examples: List[Dict[str, Any]]

class PluginResponse(BaseModel):
    plugin_id: str
    name: str
    version: str
    description: str
    agent_types: List[str]
    enabled: bool
    created_at: str

class PluginExecutionRequest(BaseModel):
    plugin_name: str
    input_data: Dict[str, Any]
    task_id: Optional[str] = None
    agent_type: Optional[str] = None

class PluginExecutionResponse(BaseModel):
    execution_id: str
    output: Optional[Dict[str, Any]]
    error: Optional[str]
    exit_code: int
    duration_ms: int
```

### Step 3: Plugin Registry Service (2 hours)

**File:** `app/services/plugin_registry.py` (new file)
```python
import json
import os
from pathlib import Path
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from app.models import Plugin, PluginPermission
from app.schemas import PluginManifest, PluginResponse

class PluginRegistry:
    """Manages plugin registration, discovery, and metadata."""
    
    def __init__(self, plugins_dir: str = "plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(exist_ok=True)
    
    async def register_plugin(self, db: AsyncSession, manifest_path: str) -> Plugin:
        """Register a new plugin from manifest file."""
        # Load and validate manifest
        with open(manifest_path) as f:
            manifest_data = json.load(f)
        manifest = PluginManifest(**manifest_data)
        
        # Check if plugin already exists
        result = await db.execute(select(Plugin).where(Plugin.name == manifest.name))
        existing = result.scalar_one_or_none()
        if existing:
            raise ValueError(f"Plugin {manifest.name} already registered")
        
        # Create plugin record
        plugin = Plugin(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            author=manifest.author,
            manifest_json=json.dumps(manifest_data),
            enabled=True
        )
        db.add(plugin)
        
        # Create permission records
        for perm_type, resources in manifest.permissions.items():
            for resource in resources:
                perm = PluginPermission(
                    plugin_id=plugin.plugin_id,
                    permission_type=perm_type,
                    resource=resource
                )
                db.add(perm)
        
        await db.commit()
        await db.refresh(plugin)
        return plugin
    
    async def get_plugins(
        self,
        db: AsyncSession,
        agent_type: Optional[str] = None,
        enabled: Optional[bool] = None
    ) -> List[Plugin]:
        """Get all plugins, optionally filtered by agent type and enabled status."""
        query = select(Plugin)
        
        if enabled is not None:
            query = query.where(Plugin.enabled == enabled)
        
        result = await db.execute(query)
        plugins = result.scalars().all()
        
        # Filter by agent_type if specified
        if agent_type:
            filtered = []
            for plugin in plugins:
                manifest = json.loads(plugin.manifest_json)
                if agent_type in manifest.get("agent_types", []):
                    filtered.append(plugin)
            return filtered
        
        return list(plugins)
    
    async def get_plugin(self, db: AsyncSession, plugin_id: str) -> Optional[Plugin]:
        """Get a single plugin by ID."""
        result = await db.execute(select(Plugin).where(Plugin.plugin_id == plugin_id))
        return result.scalar_one_or_none()
    
    async def get_plugin_by_name(self, db: AsyncSession, name: str) -> Optional[Plugin]:
        """Get a single plugin by name."""
        result = await db.execute(select(Plugin).where(Plugin.name == name))
        return result.scalar_one_or_none()
    
    async def delete_plugin(self, db: AsyncSession, plugin_id: str) -> bool:
        """Delete a plugin and its permissions."""
        plugin = await self.get_plugin(db, plugin_id)
        if not plugin:
            return False
        
        await db.delete(plugin)
        await db.commit()
        return True
    
    async def toggle_plugin(self, db: AsyncSession, plugin_id: str, enabled: bool) -> bool:
        """Enable or disable a plugin."""
        plugin = await self.get_plugin(db, plugin_id)
        if not plugin:
            return False
        
        plugin.enabled = enabled
        plugin.updated_at = datetime.utcnow().isoformat()
        await db.commit()
        return True
    
    def get_plugin_path(self, plugin_name: str) -> Path:
        """Get the filesystem path for a plugin directory."""
        return self.plugins_dir / plugin_name
    
    def load_manifest(self, plugin_name: str) -> PluginManifest:
        """Load plugin manifest from filesystem."""
        manifest_path = self.get_plugin_path(plugin_name) / "manifest.json"
        with open(manifest_path) as f:
            return PluginManifest(**json.load(f))
```

### Step 4: Plugin Executor Service (2-3 hours)

**File:** `app/services/plugin_executor.py` (new file)
```python
import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PluginExecution
from app.services.plugin_registry import PluginRegistry
from app.schemas import PluginManifest

class PluginExecutor:
    """Executes plugins in isolated subprocesses."""
    
    def __init__(self, registry: PluginRegistry):
        self.registry = registry
    
    async def execute(
        self,
        db: AsyncSession,
        plugin_name: str,
        input_data: Dict[str, Any],
        task_id: Optional[str] = None,
        agent_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """Execute a plugin and return the result."""
        start_time = time.time()
        
        # Get plugin from database
        plugin_db = await self.registry.get_plugin_by_name(db, plugin_name)
        if not plugin_db:
            raise ValueError(f"Plugin {plugin_name} not found")
        
        if not plugin_db.enabled:
            raise ValueError(f"Plugin {plugin_name} is disabled")
        
        # Load manifest
        manifest = PluginManifest(**json.loads(plugin_db.manifest_json))
        
        # Validate input (TODO: JSON Schema validation)
        # For v1, just ensure it's a dict
        if not isinstance(input_data, dict):
            raise ValueError("Input must be a JSON object")
        
        # Prepare execution environment
        plugin_dir = self.registry.get_plugin_path(plugin_name)
        executable_path = plugin_dir / manifest.executable
        
        if not executable_path.exists():
            raise FileNotFoundError(f"Plugin executable not found: {executable_path}")
        
        # Build command
        cmd = [manifest.runtime, str(executable_path), json.dumps(input_data)]
        
        # Execute with timeout
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(plugin_dir)
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=manifest.timeout_seconds
                )
                exit_code = process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise TimeoutError(f"Plugin {plugin_name} exceeded timeout of {manifest.timeout_seconds}s")
            
            # Parse output
            output_data = None
            error_message = None
            
            if exit_code == 0:
                try:
                    output_data = json.loads(stdout.decode())
                except json.JSONDecodeError as e:
                    error_message = f"Invalid JSON output: {e}"
                    exit_code = 1
            else:
                error_message = stderr.decode() or "Plugin execution failed"
            
            # Record execution
            duration_ms = int((time.time() - start_time) * 1000)
            execution = PluginExecution(
                plugin_id=plugin_db.plugin_id,
                task_id=task_id,
                agent_type=agent_type,
                input_json=json.dumps(input_data),
                output_json=json.dumps(output_data) if output_data else None,
                error_message=error_message,
                exit_code=exit_code,
                duration_ms=duration_ms
            )
            db.add(execution)
            await db.commit()
            await db.refresh(execution)
            
            return {
                "execution_id": execution.execution_id,
                "output": output_data,
                "error": error_message,
                "exit_code": exit_code,
                "duration_ms": duration_ms
            }
            
        except Exception as e:
            # Record failed execution
            duration_ms = int((time.time() - start_time) * 1000)
            execution = PluginExecution(
                plugin_id=plugin_db.plugin_id,
                task_id=task_id,
                agent_type=agent_type,
                input_json=json.dumps(input_data),
                error_message=str(e),
                exit_code=-1,
                duration_ms=duration_ms
            )
            db.add(execution)
            await db.commit()
            raise
```

### Step 5: API Endpoints (2 hours)

**File:** `app/routers/plugins.py` (new file)
```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.database import get_db
from app.schemas import PluginResponse, PluginExecutionRequest, PluginExecutionResponse
from app.services.plugin_registry import PluginRegistry
from app.services.plugin_executor import PluginExecutor

router = APIRouter(prefix="/api/plugins", tags=["plugins"])
registry = PluginRegistry()
executor = PluginExecutor(registry)

@router.post("/register")
async def register_plugin(
    manifest_path: str,
    db: AsyncSession = Depends(get_db)
):
    """Register a new plugin from manifest file."""
    try:
        plugin = await registry.register_plugin(db, manifest_path)
        return {
            "plugin_id": plugin.plugin_id,
            "name": plugin.name,
            "status": "registered"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("", response_model=List[PluginResponse])
async def list_plugins(
    agent_type: Optional[str] = None,
    enabled: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    """List all plugins, optionally filtered."""
    plugins = await registry.get_plugins(db, agent_type=agent_type, enabled=enabled)
    return [
        PluginResponse(
            plugin_id=p.plugin_id,
            name=p.name,
            version=p.version,
            description=p.description,
            agent_types=json.loads(p.manifest_json)["agent_types"],
            enabled=p.enabled,
            created_at=p.created_at
        )
        for p in plugins
    ]

@router.get("/{plugin_id}")
async def get_plugin(
    plugin_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get plugin details."""
    plugin = await registry.get_plugin(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    
    # TODO: Add execution stats
    return {
        "plugin_id": plugin.plugin_id,
        "name": plugin.name,
        "version": plugin.version,
        "manifest": json.loads(plugin.manifest_json),
        "enabled": plugin.enabled
    }

@router.delete("/{plugin_id}")
async def delete_plugin(
    plugin_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a plugin."""
    success = await registry.delete_plugin(db, plugin_id)
    if not success:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return {"status": "deleted"}

@router.post("/execute", response_model=PluginExecutionResponse)
async def execute_plugin(
    request: PluginExecutionRequest,
    db: AsyncSession = Depends(get_db)
):
    """Execute a plugin (internal use)."""
    try:
        result = await executor.execute(
            db,
            plugin_name=request.plugin_name,
            input_data=request.input_data,
            task_id=request.task_id,
            agent_type=request.agent_type
        )
        return PluginExecutionResponse(**result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
```

**File:** `app/main.py` (modify to include plugins router)
```python
# ... existing imports ...
from app.routers import plugins  # Add this

# ... existing code ...

app.include_router(plugins.router)  # Add this
```

### Step 6: Create Example Plugin (30 min)

**Directory:** `plugins/echo-test/`

**File:** `plugins/echo-test/manifest.json`
```json
{
  "name": "echo-test",
  "version": "1.0.0",
  "description": "Simple echo plugin for testing",
  "agent_types": ["programmer", "researcher", "writer", "architect", "project-manager"],
  "author": "lobs-team",
  "executable": "execute.py",
  "runtime": "python3",
  "timeout_seconds": 5,
  "permissions": {
    "network": [],
    "filesystem_read": [],
    "filesystem_write": [],
    "environment_vars": []
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "message": {"type": "string", "description": "Message to echo back"}
    },
    "required": ["message"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "echoed": {"type": "string"},
      "length": {"type": "integer"}
    }
  },
  "examples": [
    {
      "input": {"message": "Hello, plugins!"},
      "output": {"echoed": "Hello, plugins!", "length": 15}
    }
  ]
}
```

**File:** `plugins/echo-test/execute.py`
```python
#!/usr/bin/env python3
import sys
import json

def main():
    # Parse input
    input_data = json.loads(sys.argv[1])
    message = input_data['message']
    
    # Echo logic
    result = {
        "echoed": message,
        "length": len(message)
    }
    
    # Return JSON output
    print(json.dumps(result))
    sys.exit(0)

if __name__ == "__main__":
    main()
```

Make executable:
```bash
chmod +x plugins/echo-test/execute.py
```

### Step 7: Tests (2-3 hours)

**File:** `tests/test_plugin_registry.py` (new file)
```python
import pytest
from app.services.plugin_registry import PluginRegistry
from app.models import Plugin

@pytest.mark.asyncio
async def test_register_plugin(db):
    registry = PluginRegistry()
    plugin = await registry.register_plugin(db, "plugins/echo-test/manifest.json")
    
    assert plugin.name == "echo-test"
    assert plugin.version == "1.0.0"
    assert plugin.enabled is True

@pytest.mark.asyncio
async def test_get_plugins_by_agent_type(db):
    registry = PluginRegistry()
    await registry.register_plugin(db, "plugins/echo-test/manifest.json")
    
    plugins = await registry.get_plugins(db, agent_type="programmer")
    assert len(plugins) >= 1
    assert any(p.name == "echo-test" for p in plugins)

@pytest.mark.asyncio
async def test_delete_plugin(db):
    registry = PluginRegistry()
    plugin = await registry.register_plugin(db, "plugins/echo-test/manifest.json")
    
    success = await registry.delete_plugin(db, plugin.plugin_id)
    assert success is True
    
    deleted = await registry.get_plugin(db, plugin.plugin_id)
    assert deleted is None
```

**File:** `tests/test_plugin_executor.py` (new file)
```python
import pytest
from app.services.plugin_registry import PluginRegistry
from app.services.plugin_executor import PluginExecutor

@pytest.mark.asyncio
async def test_execute_plugin_success(db):
    registry = PluginRegistry()
    executor = PluginExecutor(registry)
    
    # Register echo plugin
    await registry.register_plugin(db, "plugins/echo-test/manifest.json")
    
    # Execute
    result = await executor.execute(
        db,
        plugin_name="echo-test",
        input_data={"message": "Hello!"},
        agent_type="programmer"
    )
    
    assert result["exit_code"] == 0
    assert result["output"]["echoed"] == "Hello!"
    assert result["output"]["length"] == 6

@pytest.mark.asyncio
async def test_execute_plugin_timeout(db):
    # TODO: Create slow-plugin for testing timeout
    pass

@pytest.mark.asyncio
async def test_execute_disabled_plugin(db):
    registry = PluginRegistry()
    executor = PluginExecutor(registry)
    
    plugin = await registry.register_plugin(db, "plugins/echo-test/manifest.json")
    await registry.toggle_plugin(db, plugin.plugin_id, enabled=False)
    
    with pytest.raises(ValueError, match="disabled"):
        await executor.execute(db, "echo-test", {"message": "test"})
```

### Step 8: Manual Testing

```bash
# 1. Run migrations
cd /Users/lobs/lobs-server
sqlite3 lobs.db < migrations/012_add_plugin_tables.sql

# 2. Start server
./bin/run

# 3. Register plugin (in another terminal)
curl -X POST "http://localhost:8000/api/plugins/register?manifest_path=plugins/echo-test/manifest.json" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 4. List plugins
curl "http://localhost:8000/api/plugins" \
  -H "Authorization: Bearer YOUR_TOKEN"

# 5. Execute plugin
curl -X POST "http://localhost:8000/api/plugins/execute" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "plugin_name": "echo-test",
    "input_data": {"message": "Hello, plugins!"},
    "agent_type": "programmer"
  }'
```

---

## Phase 2: Orchestrator Integration

(To be documented after Phase 1 is complete)

Quick preview:
1. Modify `app/orchestrator/prompter.py` to inject plugin docs
2. Modify `app/orchestrator/worker.py` to detect and execute PLUGIN_CALL
3. Create `app/orchestrator/plugin_parser.py` for parsing agent output
4. Add integration tests

---

## Common Issues

### Issue: Migration fails
**Solution:** Check if tables already exist with `sqlite3 lobs.db ".schema plugins"`

### Issue: Plugin not found
**Solution:** Ensure `plugins/` directory exists and manifest.json is valid

### Issue: Permission denied on execute.py
**Solution:** Run `chmod +x plugins/*/execute.py`

### Issue: JSON decode error
**Solution:** Plugin must print valid JSON to stdout. Use `sys.stderr` for logs.

---

## Next Steps

After Phase 1 is complete and tested:
1. Create handoff for Phase 2 (orchestrator integration)
2. Build 2-3 useful example plugins (GitHub, code formatter, etc.)
3. Add JSON Schema validation
4. Implement permission checking
5. Write developer documentation

---

**Questions?** Check the main ADR or ask in #lobs-dev channel.
