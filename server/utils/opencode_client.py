"""Thin wrapper over `opencode serve` HTTP API.

Methods cover only what M1 needs. SSE is exposed as an iterator of
{"event": str, "data": dict} dicts so the route layer can re-emit them.
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

    def create_session(self, *, cwd: str, title: str = "") -> str:
        resp = requests.post(
            self._url("/session"),
            json={"cwd": cwd, "title": title},
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

    def register_mcp(self, *, session_id: str, url: str) -> None:
        resp = requests.post(
            self._url("/mcp"),
            json={"sessionId": session_id, "url": url, "type": "http"},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def send_prompt_async(self, opencode_session_id: str, content: str) -> None:
        resp = requests.post(
            self._url(f"/session/{opencode_session_id}/prompt_async"),
            json={"content": content},
            timeout=self.timeout,
        )
        resp.raise_for_status()

    def subscribe_events(self) -> Iterator[dict]:
        """Yield parsed SSE events. Caller is responsible for filtering by session."""
        with requests.get(
            self._url("/event"),
            stream=True,
            timeout=None,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            resp.raise_for_status()
            event_name = None
            data_buf: list[str] = []
            for raw in resp.iter_lines():
                if raw is None:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                line = line.rstrip("\r\n")
                if line == "":
                    if event_name is not None:
                        joined = "".join(data_buf)
                        try:
                            data = json.loads(joined) if joined else {}
                        except json.JSONDecodeError:
                            data = {"_raw": joined}
                        yield {"event": event_name, "data": data}
                    event_name = None
                    data_buf = []
                elif line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_buf.append(line[len("data:"):].strip())
                # other SSE fields (id:, retry:) are ignored in M1
