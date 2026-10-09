"""A local stand-in for the Anthropic Messages API that speaks the real
streaming (SSE) wire format, so tests exercise the real SDK + llm.LLM code
paths: structured outputs, web search blocks, pause_turn, refusals, errors."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

# A scripted reply is one of:
#   {"blocks": [...], "stop_reason": "end_turn", "searches": 0}
#   {"status": 400, "message": "..."}
Reply = dict
Responder = Callable[[dict, dict], Reply]  # (request_body, headers) -> reply


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def thinking_block() -> dict:
    return {"type": "thinking", "thinking": "", "signature": "sig_abc"}


def search_blocks(query: str, results: list[tuple[str, str]], tool_id: str = "srvtoolu_1") -> list[dict]:
    return [
        {"type": "server_tool_use", "id": tool_id, "name": "web_search", "input": {"query": query}},
        {"type": "web_search_tool_result", "tool_use_id": tool_id,
         "content": [{"type": "web_search_result", "url": u, "title": t, "encrypted_content": "enc",
                      "page_age": "2025-01-01"} for u, t in results]},
    ]


def search_error_blocks(tool_id: str = "srvtoolu_err") -> list[dict]:
    return [
        {"type": "server_tool_use", "id": tool_id, "name": "web_search", "input": {"query": "x"}},
        {"type": "web_search_tool_result", "tool_use_id": tool_id,
         "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}},
    ]


def stream_bytes(reply: Reply, model: str) -> bytes:
    out = [_sse("message_start", {"type": "message_start", "message": {
        "id": "msg_fake", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1000, "output_tokens": 1, "cache_read_input_tokens": 0,
                  "cache_creation_input_tokens": 0}}})]
    for i, b in enumerate(reply["blocks"]):
        if b["type"] == "text":
            out.append(_sse("content_block_start", {"type": "content_block_start", "index": i,
                                                    "content_block": {"type": "text", "text": ""}}))
            t = b["text"]
            for j in range(0, len(t), 400):  # chunk like the real API
                out.append(_sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                        "delta": {"type": "text_delta", "text": t[j:j + 400]}}))
        elif b["type"] == "thinking":
            out.append(_sse("content_block_start", {"type": "content_block_start", "index": i,
                                                    "content_block": {"type": "thinking", "thinking": "", "signature": ""}}))
            out.append(_sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                    "delta": {"type": "signature_delta", "signature": b["signature"]}}))
        elif b["type"] == "server_tool_use":
            out.append(_sse("content_block_start", {"type": "content_block_start", "index": i,
                                                    "content_block": {**b, "input": {}}}))
            out.append(_sse("content_block_delta", {"type": "content_block_delta", "index": i,
                                                    "delta": {"type": "input_json_delta",
                                                              "partial_json": json.dumps(b["input"])}}))
        else:
            out.append(_sse("content_block_start", {"type": "content_block_start", "index": i, "content_block": b}))
        out.append(_sse("content_block_stop", {"type": "content_block_stop", "index": i}))
    usage = {"output_tokens": 500}
    if reply.get("searches"):
        usage["server_tool_use"] = {"web_search_requests": reply["searches"]}
    delta = {"stop_reason": reply.get("stop_reason", "end_turn"), "stop_sequence": None}
    msg_delta = {"type": "message_delta", "delta": delta, "usage": usage}
    if reply.get("stop_reason") == "refusal":
        delta["stop_details"] = {"type": "refusal", "category": None, "explanation": "declined"}
    out.append(_sse("message_delta", msg_delta))
    out.append(_sse("message_stop", {"type": "message_stop"}))
    return b"".join(out)


class FakeAnthropic:
    def __init__(self, responder: Responder):
        self.responder = responder
        self.requests: list[tuple[dict, dict]] = []
        owner = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers["content-length"])
                body = json.loads(self.rfile.read(n))
                headers = {k.lower(): v for k, v in self.headers.items()}
                owner.requests.append((body, headers))
                reply = owner.responder(body, headers)
                if "status" in reply:
                    payload = json.dumps({"type": "error", "error": {
                        "type": "invalid_request_error" if reply["status"] == 400 else "api_error",
                        "message": reply["message"]}}).encode()
                    self.send_response(reply["status"])
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                data = stream_bytes(reply, body.get("model", "claude-opus-5-5"))
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
