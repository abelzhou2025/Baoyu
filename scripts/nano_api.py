#!/usr/bin/env python3
"""OpenAI-compatible client for text/image generation with retries."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _config() -> Dict[str, str]:
    base = _env("NANO_BASE_URL", "https://ssl.52youxi.cc")
    path = _env("NANO_CHAT_PATH", "/v1/chat/completions")
    return {
        "api_key": _env("NANO_API_KEY", ""),
        "base_url": base.rstrip("/"),
        "chat_path": path if path.startswith("/") else f"/{path}",
        "model": _env("NANO_MODEL", "gemini-3-pro-image-preview"),
        "text_model": _env("NANO_TEXT_MODEL", _env("NANO_MODEL", "gemini-3-pro-image-preview")),
        "image_model": _env("NANO_IMAGE_MODEL", _env("NANO_MODEL", "gemini-3-pro-image-preview")),
    }


def _request_json(payload: Dict[str, Any], timeout: int = 120, retries: int = 2, model: Optional[str] = None) -> Dict[str, Any]:
    cfg = _config()
    if not cfg["api_key"]:
        raise RuntimeError("NANO_API_KEY is not set")

    if model:
        payload["model"] = model
    elif "model" not in payload:
        payload["model"] = cfg["model"]

    urls = [f"{cfg['base_url']}{cfg['chat_path']}"]
    # Fallback for providers with typo endpoint routing.
    if cfg["chat_path"] == "/v1/chat/completions":
        urls.append(f"{cfg['base_url']}/v1/caht/completions")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_err: Optional[Exception] = None
    for url in urls:
        for i in range(retries + 1):
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {cfg['api_key']}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8", errors="replace"))
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
                if i < retries:
                    time.sleep(1.2 * (i + 1))
                continue

    raise RuntimeError(f"nano api request failed: {last_err}")


def _extract_text(resp: Dict[str, Any]) -> str:
    choices = resp.get("choices") or []
    if not choices:
        return ""
    msg = (choices[0] or {}).get("message") or {}
    content = msg.get("content")

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("type")
                if t in {"text", "output_text"} and item.get("text"):
                    chunks.append(item["text"])
                elif item.get("content") and isinstance(item["content"], str):
                    chunks.append(item["content"])
        return "\n".join(c.strip() for c in chunks if c.strip()).strip()

    return ""


def _extract_image_bytes(resp: Dict[str, Any]) -> Optional[bytes]:
    choices = resp.get("choices") or []
    if not choices:
        return None
    msg = (choices[0] or {}).get("message") or {}

    images = msg.get("images") or []
    if isinstance(images, list):
        for im in images:
            if isinstance(im, dict):
                b64 = im.get("b64_json") or im.get("base64") or im.get("data")
                if b64:
                    return base64.b64decode(b64)

    content = msg.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            b64 = item.get("b64_json") or item.get("base64") or item.get("data")
            if b64:
                return base64.b64decode(b64)
            text = item.get("text") or ""
            m = re.search(r"data:image/\w+;base64,([A-Za-z0-9+/=]+)", text)
            if m:
                return base64.b64decode(m.group(1))

    text = _extract_text(resp)
    if text:
        m = re.search(r"data:image/\w+;base64,([A-Za-z0-9+/=]+)", text)
        if m:
            return base64.b64decode(m.group(1))

        u = re.search(r"https?://\S+\.(?:png|jpg|jpeg|webp)", text)
        if u:
            with urllib.request.urlopen(u.group(0), timeout=60) as r:
                return r.read()

    return None


def chat_generate(prompt: str, system: str = "", model: Optional[str] = None, max_tokens: int = 3500) -> str:
    msgs: List[Dict[str, str]] = []
    if system.strip():
        msgs.append({"role": "system", "content": system.strip()})
    msgs.append({"role": "user", "content": prompt})

    payload = {
        "messages": msgs,
        "temperature": 0.4,
        "max_tokens": max_tokens,
    }
    r = _request_json(payload, model=model or _config()["text_model"])
    out = _extract_text(r)
    if not out:
        raise RuntimeError("nano api returned empty text")
    return out


def image_generate(prompt: str, out_path: Path, model: Optional[str] = None) -> None:
    msgs = [{"role": "user", "content": prompt}]
    payload = {
        "messages": msgs,
        "temperature": 0.2,
        "max_tokens": 1200,
        "modalities": ["text", "image"],
    }
    r = _request_json(payload, timeout=180, retries=1, model=model or _config()["image_model"])
    raw = _extract_image_bytes(r)
    if not raw:
        raise RuntimeError("nano api did not return image bytes")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_chat = sub.add_parser("chat")
    p_chat.add_argument("--prompt", help="prompt text")
    p_chat.add_argument("--prompt-file", help="prompt file")
    p_chat.add_argument("--system", default="")
    p_chat.add_argument("--system-file")
    p_chat.add_argument("--output")
    p_chat.add_argument("--model")

    p_img = sub.add_parser("image")
    p_img.add_argument("--prompt", help="prompt text")
    p_img.add_argument("--prompt-file", help="prompt file")
    p_img.add_argument("--output", required=True)
    p_img.add_argument("--model")

    args = p.parse_args()

    if args.cmd == "chat":
        prompt = args.prompt or (args.prompt_file and _read(args.prompt_file)) or ""
        system = args.system or (args.system_file and _read(args.system_file)) or ""
        if not prompt.strip():
            raise SystemExit("missing prompt")
        out = chat_generate(prompt=prompt, system=system, model=args.model)
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)
        return 0

    prompt = args.prompt or (args.prompt_file and _read(args.prompt_file)) or ""
    if not prompt.strip():
        raise SystemExit("missing prompt")
    image_generate(prompt=prompt, out_path=Path(args.output), model=args.model)
    print(json.dumps({"status": "ok", "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
