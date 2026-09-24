# Source from the Quiver interactive shell wrapper; never execute directly.
if not set -q QUIVER_AUDIT_HOOK_LOADED
set -gx QUIVER_AUDIT_HOOK_LOADED 1
set -g _quiver_command_started_at ''
set -g _quiver_command_text ''

function _quiver_preexec --on-event fish_preexec
    set -g _quiver_command_started_at (date +%s.%N)
    set -g _quiver_command_text $argv[1]
end

function _quiver_postexec --on-event fish_postexec
    test -n "$QUIVER_SESSION_ID"; or return
    test -n "$_quiver_command_text"; or return
    set -l command_file "/workspace/.logs/sessions/$QUIVER_SESSION_ID/commands.jsonl"
    python -c '
import json
import sys
import time
from datetime import UTC, datetime

path, command, exit_code, cwd, started = sys.argv[1:]
try:
    duration = max(0, int((time.time() - float(started)) * 1000))
except ValueError:
    duration = 0
with open(path, "a", encoding="utf-8") as output:
    output.write(json.dumps({"event": "command", "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "cwd": cwd, "command": command, "exit": int(exit_code), "duration_ms": duration}) + "\n")
' "$command_file" "$_quiver_command_text" "$status" "$PWD" "$_quiver_command_started_at"
    set -g _quiver_command_text ''
end
end
