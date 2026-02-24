# Plugins Directory

This directory contains custom agent skills that extend the capabilities of Lobs agents.

## Structure

Each plugin is a directory with:
- `manifest.json` - Plugin metadata and configuration
- `execute.py` (or other executable) - The plugin logic
- `README.md` (optional) - Plugin documentation

## Example Plugin

See `echo-test/` for a minimal working example.

## Creating a Plugin

### 1. Create plugin directory
```bash
mkdir plugins/my-plugin
```

### 2. Create manifest.json

```json
{
  "name": "my-plugin",
  "version": "1.0.0",
  "description": "What this plugin does",
  "agent_types": ["programmer", "researcher"],
  "author": "your-email@example.com",
  "executable": "execute.py",
  "runtime": "python3",
  "timeout_seconds": 30,
  "permissions": {
    "network": ["api.example.com"],
    "filesystem_read": [],
    "filesystem_write": [],
    "environment_vars": ["API_KEY"]
  },
  "input_schema": {
    "type": "object",
    "properties": {
      "param1": {"type": "string", "description": "First parameter"}
    },
    "required": ["param1"]
  },
  "output_schema": {
    "type": "object",
    "properties": {
      "result": {"type": "string"}
    }
  },
  "examples": [
    {
      "input": {"param1": "test"},
      "output": {"result": "success"}
    }
  ]
}
```

### 3. Create executable script

```python
#!/usr/bin/env python3
import sys
import json

def main():
    # Parse input
    input_data = json.loads(sys.argv[1])
    
    # Your plugin logic here
    result = {"result": "success"}
    
    # Print JSON output
    print(json.dumps(result))
    sys.exit(0)

if __name__ == "__main__":
    main()
```

### 4. Make executable
```bash
chmod +x plugins/my-plugin/execute.py
```

### 5. Test locally
```bash
cd plugins/my-plugin
python3 execute.py '{"param1": "test"}'
```

### 6. Register with server
```bash
curl -X POST "http://localhost:8000/api/plugins/register?manifest_path=plugins/my-plugin/manifest.json" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Plugin Contract

**Input:** JSON string as first command-line argument  
**Output:** JSON to stdout  
**Errors:** Messages to stderr  
**Exit code:** 0 for success, non-zero for failure  

## Available Plugins

- **echo-test** - Simple echo plugin for testing (all agents)

## Documentation

For full documentation on the plugin system architecture and development guide, see:
- [docs/plugin-system-ADR.md](../docs/plugin-system-ADR.md) - Architecture Decision Record
- [docs/plugin-system-implementation-guide.md](../docs/plugin-system-implementation-guide.md) - Implementation guide

## Status

⚠️ **DESIGN PHASE** - Plugin system is designed but not yet implemented in the orchestrator. See ADR for implementation roadmap.
