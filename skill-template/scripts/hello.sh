#!/bin/bash
# Example Bash skill script

# Read JSON from stdin
INPUT=$(cat)

# Parse JSON using jq (if available) or python
if command -v jq &> /dev/null; then
    NAME=$(echo "$INPUT" | jq -r '.input.name // "World"')
    PREFIX=$(echo "$INPUT" | jq -r '.config.greeting_prefix // "Hello"')
else
    # Fallback to python for JSON parsing
    NAME=$(echo "$INPUT" | python3 -c "import json,sys; data=json.load(sys.stdin); print(data.get('input',{}).get('name','World'))")
    PREFIX=$(echo "$INPUT" | python3 -c "import json,sys; data=json.load(sys.stdin); print(data.get('config',{}).get('greeting_prefix','Hello'))")
fi

# Return JSON result
cat <<EOF
{
  "message": "$PREFIX, $NAME! This is a Bash skill.",
  "data": {
    "greeted_name": "$NAME",
    "language": "bash"
  }
}
EOF
