"""Tiny demo RAG service for trying TestMyLLM's live RAG evaluation (stdlib only, no LLM).

POST /ask {"question": "..."} -> {"answer": "...", "sources": [{"id": ..., "text": ...}, ...]}

Retrieval is naive keyword overlap over a few support-policy documents; the "answer" is the best
document's text. It is deliberately imperfect: the warranty document is missing from the index, so
warranty questions show up as RETRIEVAL failures in the failure-origin breakdown.

Run: python app.py  (listens on :8090)
"""
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOCS = [
    {"id": "returns", "text": "Return policy: unused products may be returned within 30 days of delivery."},
    {"id": "refunds", "text": "Refunds are issued to the original payment method within 5 business days."},
    {"id": "shipping", "text": "Shipping: we deliver to 40 countries. International delivery takes 7-14 business days."},
    {"id": "address", "text": "Orders can be edited, including the delivery address, until they are shipped."},
    # warranty document intentionally NOT indexed
]
STOP = {"the", "a", "an", "do", "you", "is", "are", "my", "i", "to", "of", "on", "can", "how", "what", "when", "there", "for", "after"}


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP}


def retrieve(q: str, k: int = 2) -> list[dict]:
    qt = tokens(q)
    scored = sorted(DOCS, key=lambda d: len(qt & tokens(d["text"])), reverse=True)
    return scored[:k]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        if self.path != "/ask":
            self.send_error(404)
            return
        length = min(int(self.headers.get("content-length", 0)), 100_000)
        try:
            q = json.loads(self.rfile.read(length) or b"{}").get("question", "")
        except json.JSONDecodeError:
            self.send_error(400, "invalid JSON")
            return
        sources = retrieve(q)
        body = json.dumps({"answer": sources[0]["text"] if sources else "I don't know.", "sources": sources}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()
