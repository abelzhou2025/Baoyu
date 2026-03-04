#!/usr/bin/env python3
"""Generate a Chinese fallback draft when online translation fails."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def pick_points(analysis: Dict[str, Any], limit: int = 5) -> List[str]:
    points = []
    for item in analysis.get("key_points", []):
        point = (item or {}).get("point", "").strip()
        if point:
            points.append(point)
        if len(points) >= limit:
            break
    return points


def maybe_translate_to_zh(text: str) -> str:
    if not text.strip():
        return text
    url = (
        "https://translate.googleapis.com/translate_a/single"
        "?client=gtx&sl=auto&tl=zh-CN&dt=t&q="
        + urllib.parse.quote(text)
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        translated = "".join(seg[0] for seg in payload[0] if seg and seg[0])
        return translated.strip() or text
    except Exception:
        return text


def build_markdown(title: str, points: List[str], output_type: str) -> str:
    heading = {
        "interpretation": "图文解读",
        "infographic": "信息图脚本",
        "xhs": "小红书图组脚本",
    }.get(output_type, "图文解读")

    if output_type == "xhs":
        section_header = "## 每页文案草案（中文）"
    elif output_type == "infographic":
        section_header = "## 信息图要点（中文）"
    else:
        section_header = "## 核心观点（中文）"

    zh_points = [maybe_translate_to_zh(p) for p in points]
    point_lines = "\n".join([f"- {p}" for p in zh_points]) if zh_points else "- 暂未提取到稳定观点，请人工补充。"

    return (
        f"# {title}｜{heading}\n\n"
        "## 说明\n"
        "- 自动翻译服务本次不可用，以下为中文兜底版。\n"
        "- 为保证准确性，已保留部分原文关键词，请发布前做一次人工润色。\n\n"
        f"{section_header}\n"
        f"{point_lines}\n\n"
        "## 一句话总结\n"
        "先发布可读版本，再做细节打磨，保证持续输出。\n\n"
        "## CTA\n"
        "你最关心哪一点？我可以继续补充成完整版中文解读。\n"
    )


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--analysis", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    analysis = read_json(Path(args.analysis))
    meta = read_json(Path(args.meta))
    brief = read_json(Path(args.brief))

    title = meta.get("title", "Untitled")
    output_type = brief.get("output_type", "interpretation")
    points = pick_points(analysis)

    md = build_markdown(title=title, points=points, output_type=output_type)
    Path(args.output).write_text(md, encoding="utf-8")
    print(json.dumps({"status": "ok", "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
