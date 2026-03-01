#!/usr/bin/env python3
"""Example skill script that fetches data from a URL."""

import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
        input_params = data.get("input", {})
    except json.JSONDecodeError:
        print(json.dumps({"message": "Invalid JSON input"}))
        sys.exit(1)

    url = input_params.get("url")
    if not url:
        print(json.dumps({"message": "URL parameter is required"}))
        sys.exit(1)

    try:
        import requests
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        result = {
            "message": f"Successfully fetched {url}",
            "data": {
                "status_code": response.status_code,
                "content_length": len(response.content),
                "content_type": response.headers.get("content-type", "unknown"),
            }
        }
        print(json.dumps(result))

    except ImportError:
        print(json.dumps({
            "message": "requests library not installed. Add 'requests' to requirements.txt"
        }))
        sys.exit(1)
    except Exception as e:
        print(json.dumps({
            "message": f"Failed to fetch {url}: {str(e)}"
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
