# Source from the Quiver interactive shell wrapper; never execute directly.
if [[ -n ${QUIVER_AUDIT_HOOK_LOADED:-} ]]; then
    return
fi
export QUIVER_AUDIT_HOOK_LOADED=1

_quiver_command_started_at=''
_quiver_command_text=''

function _quiver_preexec() {
    _quiver_command_started_at=$EPOCHREALTIME
    _quiver_command_text=$1
}

function _quiver_precmd() {
    local status=$?
    [[ -z ${QUIVER_SESSION_ID:-} ]] && return
    local command_file="/workspace/.logs/sessions/${QUIVER_SESSION_ID}/commands.jsonl"
    [[ -z $_quiver_command_text ]] && return
    python - "$command_file" "$_quiver_command_text" "$status" "$PWD" "$_quiver_command_started_at" <<'PYTHON'
import json
import sys
from datetime import UTC, datetime

path, command, exit_code, cwd, started = sys.argv[1:]
now = datetime.now(UTC)
try:
    duration = max(0, int((float(__import__('time').time()) - float(started)) * 1000))
except ValueError:
    duration = 0
with open(path, "a", encoding="utf-8") as output:
    output.write(json.dumps({"event": "command", "ts": now.isoformat().replace("+00:00", "Z"), "cwd": cwd, "command": command, "exit": int(exit_code), "duration_ms": duration}) + "\n")
PYTHON
    _quiver_command_text=''
}

autoload -Uz add-zsh-hook
add-zsh-hook preexec _quiver_preexec
add-zsh-hook precmd _quiver_precmd
