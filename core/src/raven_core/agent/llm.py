"""Plans from a model when the fast-path grammar does not match (section 9.10, steps 2 and 3).

T2: the local model writes the plan as JSON (Ollama format = schema).
T3: a cloud model writes the plan from redacted context: folder names, types and the
command, never file contents. The cloud cannot call tools. Raven resolves the plan locally.
"""

from __future__ import annotations

import json
import os
import re

from ..privacy import gate
from ..router import cloud, local
from ..router import router as core

OPS = ["find", "selection", "convert", "move", "copy", "rename", "mkdir", "trash", "compress", "extract"]
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": OPS},
                    "input": {"type": "integer"},
                    "folder": {"type": "string"},
                    "types": {"type": "array", "items": {"type": "string"}},
                    "name": {"type": "string"},
                    "about": {"type": "string"},
                    "to": {"type": "string"},
                    "in": {"type": "string"},
                },
                "required": ["op"],
            },
        },
        "error": {"type": "string"},
    },
    "required": ["steps"],
}

SYSTEM = """You turn a file command into a plan of steps for a file manager. You cannot run anything. Reply with JSON only.
Steps (numbered from 1 in order):
- {"op":"find","folder":"<folder name or path>","types":["pdf"],"name":"<file name>","about":"<topic>"}: pick files. Leave out the parts you do not need.
- {"op":"selection"}: the files that the user selected.
- {"op":"convert","input":<step>,"to":"pdf"}
- {"op":"move","input":<step>,"to":"<folder>"} and {"op":"copy","input":<step>,"to":"<folder>"}. A missing folder is created.
- {"op":"rename","input":<step>,"to":"<new name; {n} is a counter, {name} the old name>"}
- {"op":"mkdir","to":"<name>","in":"<parent folder>"}
- {"op":"trash","input":<step>}
- {"op":"compress","input":<step>,"to":"7z"}
- {"op":"extract","input":<step>,"to":"<folder>"}
"input" is the number of the step whose files this step uses.
Example: "convert the word files in Sem5 to pdf and copy them to Submissions" ->
{"steps":[{"op":"find","folder":"Sem5","types":["docx","doc"]},{"op":"convert","input":1,"to":"pdf"},{"op":"copy","input":2,"to":"Submissions"}]}
Example: "put my bank statements in a folder called Bank" ->
{"steps":[{"op":"find","about":"bank statements"},{"op":"move","input":1,"to":"Bank"}]}
Use "about" for a topic and "name" only for an exact file name such as report.docx. Leave "folder" empty for the folder in view.
If the command is not a file command, reply {"steps":[],"error":"<one short sentence>"}.
Placeholders such as [PERSON_1] stand for private names. Copy them exactly."""


def _context(cwd: str, selection: list[str], red: gate.Redactor | None) -> str:
    def r(x: str) -> str:
        return red.redact(x, name=True) if red else x
    lines = [f"Folder in view: {r(os.path.basename(cwd) or cwd)}"]
    try:
        subs = sorted(d.name for d in os.scandir(cwd) if d.is_dir() and not d.name.startswith("."))[:40]
    except OSError:
        subs = []
    if subs:
        lines.append("Folders in it: " + ", ".join(r(s) for s in subs))
    if selection:
        lines.append("Selected: " + ", ".join(r(os.path.basename(s)) for s in selection[:20]))
    return "\n".join(lines)


def _restore(steps: list[dict], mapping: dict[str, str]) -> list[dict]:
    out = []
    for st in steps:
        out.append({k: (gate.restore(v, mapping) if isinstance(v, str) else v) for k, v in st.items()})
    return out


def _clean(data: dict) -> tuple[list[dict], str]:
    steps = [s for s in data.get("steps") or [] if isinstance(s, dict) and s.get("op") in OPS]
    return steps, str(data.get("error") or "")


def plan_local(command: str, cwd: str, selection: list[str]) -> tuple[list[dict], str]:
    if not local.available():
        raise local.LocalError(local.unavailable_reason())
    user = _context(cwd, selection, None) + f"\nCommand: {command}"
    data = local.chat(SYSTEM, user, PLAN_SCHEMA, max_tokens=400)
    return _clean(data)


def plan_cloud(command: str, cwd: str, selection: list[str], providers: list[str]) -> tuple[list[dict], str, str, int]:
    """Return (steps, error, provider, bytes sent). Raises cloud.CloudError when every provider fails."""
    from ..fs.props import policy_for

    if policy_for(cwd, "no_cloud") or any(policy_for(s, "no_cloud") for s in selection):
        raise cloud.CloudError("a folder rule keeps this folder off the cloud")
    red = gate.Redactor()
    payload = _context(cwd, selection, red) + f"\nCommand: {red.redact(command)}"
    blocked, why = gate.final_check(payload)
    if blocked:
        raise cloud.CloudError(f"the redaction gate blocked the request: {why}")
    errors = []
    for prov in providers:
        lid = gate.ledger_write("agent plan", "T3", prov, SYSTEM + "\n\n" + payload, len(red.mapping))
        try:
            data, _model = cloud.PROVIDERS[prov](SYSTEM, payload, PLAN_SCHEMA)
        except cloud.CloudError as e:
            gate.ledger_mark_failed(lid, "agent plan")
            errors.append(f"{prov}: {e}")
            continue
        steps, err = _clean(data)
        return _restore(steps, red.mapping), gate.restore(err, red.mapping), prov, len((SYSTEM + payload).encode())
    raise cloud.CloudError("; ".join(errors))


def verb_count(command: str) -> int:
    return len(re.findall(r"\b(convert|move|copy|rename|make|create|trash|delete|remove|compress|archive|zip|extract|unzip|organi[sz]e|put|send)\b",
                          command, re.I))


def providers() -> list[str]:
    if core.ai_mode() == "local":
        return []
    return core.provider_order("ask")
