#!/usr/bin/env python3
"""
Echo Test Plugin

Simple plugin that echoes back the input message.
Useful for testing the plugin system.
"""
import sys
import json

def main():
    """Main entry point for the plugin."""
    try:
        # Parse input from first argument
        if len(sys.argv) < 2:
            error = {"error": "No input provided"}
            print(json.dumps(error), file=sys.stderr)
            sys.exit(1)
        
        input_data = json.loads(sys.argv[1])
        
        # Validate required fields
        if 'message' not in input_data:
            error = {"error": "Missing required field 'message'"}
            print(json.dumps(error), file=sys.stderr)
            sys.exit(1)
        
        message = input_data['message']
        
        # Echo logic
        result = {
            "echoed": message,
            "length": len(message)
        }
        
        # Print JSON output to stdout
        print(json.dumps(result))
        sys.exit(0)
        
    except json.JSONDecodeError as e:
        error = {"error": f"Invalid JSON input: {e}"}
        print(json.dumps(error), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        error = {"error": f"Unexpected error: {e}"}
        print(json.dumps(error), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
