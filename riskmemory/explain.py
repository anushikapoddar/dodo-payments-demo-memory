"""Optional Claude composition over an already-computed assessment.

Claude never decides, never writes memory, and never invents evidence.
Keys stay in the process environment (loaded from .env) and are never returned.

Prefers an Amazon Bedrock API key (AWS_BEARER_TOKEN_BEDROCK). Falls back to
ANTHROPIC_API_KEY if that is what is present.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BEDROCK_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
SYSTEM = (
    "You explain a merchant-risk assessment for an underwriter. "
    "Use only the supplied evidence. Do not add facts, merchants, "
    "incidents, graph links, or numbers that are not listed. "
    "If precedent says no cases were retrieved, say so — do not invent names. "
    "If the open-web lookup is empty, say the public web had no corroboration. "
    "Do not change the recommendation. Two to four short sentences."
)


def load_dotenv(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE lines from .env without overriding a live environment."""
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def llm_status() -> str:
    if (os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip():
        region = _region()
        model = os.environ.get("BEDROCK_MODEL") or DEFAULT_BEDROCK_MODEL
        short = model.split(".")[-1] if "." in model else model
        return f"bedrock ({region} / {short})"
    if (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        return "anthropic"
    return "off — paste AWS_BEARER_TOKEN_BEDROCK into .env"


def explain_assessment(brief: dict) -> Optional[str]:
    """Return a short grounded summary, or None if no key / the call fails."""
    prompt = _prompt(brief)
    if not prompt:
        return None
    bedrock = (os.environ.get("AWS_BEARER_TOKEN_BEDROCK") or "").strip()
    if bedrock:
        return _bedrock(prompt, bedrock)
    anthropic = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if anthropic:
        return _anthropic(prompt, anthropic)
    return None


def _region() -> str:
    return (os.environ.get("BEDROCK_REGION")
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or "us-east-1").strip()


def _bedrock(prompt: str, token: str) -> Optional[str]:
    model = (os.environ.get("BEDROCK_MODEL") or DEFAULT_BEDROCK_MODEL).strip()
    url = f"https://bedrock-runtime.{_region()}.amazonaws.com/model/{model}/converse"
    body = json.dumps({
        "system": [{"text": SYSTEM}],
        "messages": [{"role": "user", "content": [{"text": prompt}]}],
        "inferenceConfig": {"maxTokens": 280},
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "content-type": "application/json",
            "accept": "application/json",
            "authorization": f"Bearer {token}",
        },
    )
    data = _json(req)
    if not data:
        return None
    parts = []
    for block in ((data.get("output") or {}).get("message") or {}).get("content") or []:
        if block.get("text"):
            parts.append(block["text"].strip())
    return " ".join(parts).strip() or None


def _anthropic(prompt: str, key: str) -> Optional[str]:
    model = (os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL).strip()
    body = json.dumps({
        "model": model,
        "max_tokens": 280,
        "system": SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        ANTHROPIC_URL, data=body, method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    data = _json(req)
    if not data:
        return None
    parts = []
    for block in data.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            parts.append(block["text"].strip())
    return " ".join(parts).strip() or None


def _json(req: urllib.request.Request) -> Optional[dict]:
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            exc.read()
        except OSError:
            pass
        return None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _prompt(brief: dict) -> str:
    m = brief.get("merchant") or {}
    d = brief.get("decision") or {}
    lines = [
        f"Merchant: {m.get('name')} ({m.get('category_claimed')}, {m.get('country')})",
        f"Pitch: {m.get('pitch')}",
        f"System recommendation: {d.get('headline')} (P(bad)={d.get('p_bad')}, "
        f"band uses threshold {d.get('threshold')})",
        f"Precedent note: {d.get('precedent_note')}",
        "Signals:",
    ]
    for s in brief.get("signals") or []:
        lines.append(f"- {s.get('title')}: {s.get('detail')}")
    if not brief.get("signals"):
        lines.append("- none fired")
    lines.append("Memory:")
    for mem in brief.get("memories") or []:
        lines.append(f"- ({mem.get('kind')}) {mem.get('text')}")
    if not brief.get("memories"):
        lines.append("- none retrieved")
    lines.append("Related merchants:")
    for r in brief.get("related") or []:
        lines.append(f"- {r.get('name')} [{r.get('status')}] via {', '.join(r.get('routes') or [])}")
    if not brief.get("related"):
        lines.append("- none")
    web = brief.get("web") or {}
    lines.append(f"Open web ({web.get('status') or 'skipped'}):")
    for h in web.get("hits") or []:
        lines.append(f"- {h.get('source')}: {h.get('title')} — {h.get('snippet')}")
    if not web.get("hits"):
        lines.append("- no public pages retrieved")
    return "\n".join(lines)
