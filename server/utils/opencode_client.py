"""Thin wrapper over `opencode serve` HTTP API (verified against v1.2.26).

Contracts (see spec §12):
- A session binds to a directory via the ?directory= query param. The body
  `cwd`/`directory` are ignored by OpenCode.
- prompt_async body is {parts:[{type:"text","text":...}]}.
- MCP is NOT registered through this client; each session's workspace carries
  an opencode.json with the MCP entry (see utils.workspace).

SSE is exposed as an iterator of {"event": str, "data": dict} dicts. OpenCode
emits a {type, properties} envelope; callers map event names themselves.
"""

import json
import requests
from typing import Iterator


class OpenCodeError(RuntimeError):
    pass


class OpenCodeClient:
    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def create_session(self, *, directory: str, title: str = "") -> str:
        resp = requests.post(
            self._url("/session"),
            params={"directory": directory},
            json={"title": title},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def delete_session(self, opencode_session_id: str) -> None:
        try:
            resp = requests.delete(
                self._url(f"/session/{opencode_session_id}"),
                timeout=self.timeout,
            )
            if resp.status_code not in (200, 204, 404):
                resp.raise_for_status()
        except requests.RequestException as e:
            raise OpenCodeError(str(e))

    def send_prompt_async(self, opencode_session_id: str, content: str,
                          model: str = "", directory: str = "") -> None:
        """Send a prompt. `model` ("<providerID>/<modelID>") is passed explicitly
        because OpenCode does NOT honor the per-directory opencode.json `model`
        field for prompt selection — without it the server falls back to its
        own default model.

        `directory` (absolute path) is passed as the ?directory= query param so
        this turn's tools (bash/write/edit) run with cwd=directory — i.e. the
        session's workspace. Without it OpenCode uses the server's launch cwd.
        """
        body = {"parts": [{"type": "text", "text": content}]}
        if model and "/" in model:
            provider_id, model_id = model.split("/", 1)
            body["model"] = {"providerID": provider_id, "modelID": model_id}
        params = {"directory": directory} if directory else None
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/prompt_async"),
            params=params,
            json=body,
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def list_providers(self) -> dict:
        """Return OpenCode's provider/model catalogue.

        Shape:
          { "all": [{"id", "name", "models": {<modelID>: {...}}, ...}, ...],
            "default": {<providerID>: <modelID>},
            "connected": {<providerID>: bool} }

        Used by the frontend's model picker. The /provider endpoint is not
        directory-scoped so no `directory` param.
        """
        resp = requests.get(self._url("/provider"), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def list_commands(self, directory: str = "") -> list:
        params = {"directory": directory} if directory else None
        resp = requests.get(self._url("/command"), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def list_skills(self, directory: str = "") -> list:
        params = {"directory": directory} if directory else None
        resp = requests.get(self._url("/skill"), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def run_command(self, opencode_session_id: str, command: str, arguments: str = "",
                    model: str = "", directory: str = "") -> None:
        body = {"command": command, "arguments": arguments}
        # NOTE: unlike prompt_async (which wants {providerID, modelID}), the command
        # endpoint wants `model` as a "provider/model" STRING (verified live; object -> 400).
        if model:
            body["model"] = model
        params = {"directory": directory} if directory else None
        # Unlike /prompt_async, /command blocks until the model turn completes — that
        # can be minutes. Keep a short connect timeout but no read timeout so a slow
        # command doesn't 500 the route (output still streams via SSE in parallel).
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/command"),
            params=params, json=body, timeout=(10, None),
        )
        resp.raise_for_status()

    def abort_session(self, opencode_session_id: str, directory: str = "") -> None:
        """Abort the in-flight turn for this session. OpenCode then emits a
        session.idle on the SSE so the UI clears its "thinking" state."""
        params = {"directory": directory} if directory else None
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/abort"),
            params=params, timeout=self.timeout,
        )
        resp.raise_for_status()

    def list_mcp(self, directory: str = "") -> dict:
        """Return configured MCP servers + connection status for `directory`, e.g.
        {"check-manage": {"status": "connected"}}. The un-scoped /mcp returns {}.
        """
        params = {"directory": directory} if directory else None
        resp = requests.get(self._url("/mcp"), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def subscribe_events(self, directory: str = "", read_timeout=None) -> Iterator[dict]:
        """Yield parsed SSE events as {"event": <type>, "data": <full object>}.

        OpenCode sends `data:`-only frames (no SSE `event:` line); the event name
        lives in the JSON `type` field, e.g.
            data: {"type":"message.part.updated","properties":{...}}

        Scope by ?directory=<dir> (the session's workspace). Because prompts run
        with that directory, OpenCode routes the turn's message/session events to
        the per-directory event stream; the un-scoped /event yields only global
        heartbeats. Each session has a unique workspace dir, so this stream
        carries only that session's events; callers still filter by sessionID.
        """
        params = {"directory": directory} if directory else None
        with requests.get(
            self._url("/event"),
            params=params,
            stream=True,
            timeout=read_timeout,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            data_buf: list[str] = []
            for raw in resp.iter_lines():
                if raw is None:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                line = line.rstrip("\r\n")
                if line == "":
                    if data_buf:
                        joined = "".join(data_buf)
                        try:
                            obj = json.loads(joined)
                        except json.JSONDecodeError:
                            obj = {"_raw": joined}
                        event = obj.get("type", "") if isinstance(obj, dict) else ""
                        yield {"event": event, "data": obj}
                    data_buf = []
                elif line.startswith("data:"):
                    data_buf.append(line[len("data:"):].strip())
                # event:/id:/retry: lines are not emitted by OpenCode; ignored
