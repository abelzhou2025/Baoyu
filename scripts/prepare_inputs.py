#!/usr/bin/env python3
"""Prepare meta.json and brief.json from a markdown source file."""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path
from typing import Dict, Tuple


def parse_frontmatter(md: str) -> Tuple[Dict[str, str], str]:
    text = md.lstrip("\ufeff")
    if not text.startswith("---\n"):
        return {}, md

    lines = text.splitlines()
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, md

    front_lines = lines[1:end_idx]
    body_lines = lines[end_idx + 1 :]

    meta: Dict[str, str] = {}
    for line in front_lines:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")

    body = "\n".join(body_lines).strip()
    return meta, body


def guess_title(front: Dict[str, str], body: str, fallback: str) -> str:
    for key in ("title", "og:title"):
        if front.get(key):
            return front[key]

    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()

    return fallback


def guess_author(front: Dict[str, str]) -> str:
    for key in ("author", "by", "creator"):
        if front.get(key):
            return front[key]
    return "Unknown"


def guess_published(front: Dict[str, str]) -> str:
    for key in ("published", "published_at", "date", "pubdate"):
        if front.get(key):
            return front[key]
    return date.today().isoformat()


def guess_language(text: str) -> str:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if cjk >= latin:
        return "zh"
    return "en"


def write_json(path: Path, data: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--source-type", required=True, choices=["blog", "article", "video"])
    p.add_argument("--audience", default="内容创作者与知识型博主")
    p.add_argument("--tone", default="专业但口语化")
    p.add_argument("--output-type", default="interpretation", choices=["interpretation", "infographic", "xhs"])
    p.add_argument("--target-channel", default="wechat", choices=["wechat", "x", "none"])
    p.add_argument("--meta", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--clean-source", help="optional cleaned source output path")
    args = p.parse_args()

    source_path = Path(args.source)
    md = source_path.read_text(encoding="utf-8")
    front, body = parse_frontmatter(md)

    title = guess_title(front, body, "Untitled Source")
    author = guess_author(front)
    published = guess_published(front)
    lang = guess_language(body or md)

    meta = {
        "title": title,
        "source_type": args.source_type,
        "source_url": args.url,
        "author": author,
        "language": lang,
        "published_at": published,
    }

    brief = {
        "audience": args.audience,
        "tone": args.tone,
        "output_type": args.output_type,
        "target_channel": args.target_channel,
    }

    write_json(Path(args.meta), meta)
    write_json(Path(args.brief), brief)

    if args.clean_source:
        Path(args.clean_source).write_text(body or md, encoding="utf-8")

    print(json.dumps({"status": "ok", "meta": meta, "brief": brief}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
