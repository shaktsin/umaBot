#!/usr/bin/env python3
"""Example Python skill script."""

import json
import sys


def main():
    # Read input from stdin (JSON format)
    try:
        data = json.load(sys.stdin)
        input_params = data.get("input", {})
        config = data.get("config", {})
    except json.JSONDecodeError:
        print(json.dumps({
            "message": "Invalid JSON input"
        }))
        sys.exit(1)

    # Get the name parameter
    name = input_params.get("name", "World")

    # Access skill configuration (if any)
    greeting_prefix = config.get("greeting_prefix", "Hello")

    # Return result as JSON
    result = {
        "message": f"{greeting_prefix}, {name}! This is a Python skill.",
        "data": {
            "greeted_name": name,
            "language": "python",
        }
    }

    print(json.dumps(result))


if __name__ == "__main__":
    main()
