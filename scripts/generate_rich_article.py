#!/usr/bin/env python3
"""Generate a richer Chinese article draft aligned with sample-style structure."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

try:
    from nano_api import chat_generate
except Exception:
    chat_generate = None  # type: ignore

MAX_IMAGES = 3
INLINE_FIGURES = max(0, MAX_IMAGES - 1)


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_text(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def maybe_translate_to_zh(text: str) -> str:
    if not text.strip():
        return text
    if re.search(r"[\u4e00-\u9fff]", text):
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


def pick_points(analysis: Dict[str, Any], limit: int = 6) -> List[str]:
    points: List[str] = []
    seen = set()
    for item in analysis.get("key_points", []):
        point = normalize_text((item or {}).get("point", ""))
        if not point:
            continue
        if point in seen:
            continue
        seen.add(point)
        points.append(maybe_translate_to_zh(point))
        if len(points) >= limit:
            break
    return points


def pick_quotes(analysis: Dict[str, Any], limit: int = 3) -> List[str]:
    quotes = []
    for q in analysis.get("evidence_quotes", []):
        s = normalize_text(q)
        if len(s) < 24:
            continue
        quotes.append(maybe_translate_to_zh(s[:140]))
        if len(quotes) >= limit:
            break
    return quotes


def extract_chapters(source_md: str, limit: int = 8) -> List[str]:
    chapters: List[str] = []
    for raw in source_md.splitlines():
        line = normalize_text(raw)
        m = re.match(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)$", line)
        if not m:
            continue
        stamp, topic = m.group(1), m.group(2)
        chapters.append(f"{stamp} {maybe_translate_to_zh(topic)}")
        if len(chapters) >= limit:
            break
    return chapters


def build_article(title: str, points: List[str], quotes: List[str], chapters: List[str], source_url: str) -> str:
    if not points:
        points = ["围绕核心问题先给结论，再给证据，最后给行动建议。"]

    top5 = points[:5]
    deep_sections = points[: max(3, INLINE_FIGURES)]

    lines: List[str] = []
    lines.append(f"# {title}｜深度解读")
    lines.append("")
    lines.append("![封面图](images/cover.png)")
    lines.append("")
    lines.append("## 导语")
    lines.append("这篇内容真正有价值的地方，不在于口号式判断，而在于它给出了可执行的判断框架：先定义问题，再看证据，最后回到业务落地。")
    lines.append("")
    lines.append("## 要点速览")
    for p in top5:
        lines.append(f"- {p}")
    lines.append("")

    lines.append("## 深度解读")
    for idx, p in enumerate(deep_sections, start=1):
        lines.append("")
        lines.append(f"### 0{idx}｜{p}")
        lines.append("- 为什么重要：这决定了你接下来是做概念讨论，还是进入可执行的产品动作。")
        lines.append("- 现实影响：团队资源配置、迭代节奏、评估指标都会随之改变。")
        quote = quotes[idx - 1] if idx - 1 < len(quotes) else "先拿到一个能落地的最小闭环，再做规模化优化。"
        lines.append(f"> 关键证据：{quote}")
        if idx <= INLINE_FIGURES:
            lines.append(f"![配图{idx}](images/figure-{idx}.png)")

    if chapters:
        lines.append("")
        lines.append("## 访谈时间线")
        for ch in chapters:
            lines.append(f"- {ch}")

    lines.append("")
    lines.append("## 可执行清单")
    lines.append("1. 先定义一个你要优化的真实业务场景（不要先追模型参数）。")
    lines.append("2. 用一周时间做最小闭环：输入、处理、输出、验收标准。")
    lines.append("3. 建立评估看板：质量、速度、成本三条线同时看。")
    lines.append("4. 先求稳定可复用，再追求炫技效果。")

    lines.append("")
    lines.append("## 结语")
    lines.append("真正拉开差距的不是‘谁先喊出趋势’，而是谁先把趋势变成稳定生产流程。")
    lines.append("")
    lines.append("## 原始来源")
    lines.append(f"- {source_url}")

    return "\n".join(lines) + "\n"


def build_image_prompts(title: str, points: List[str]) -> Dict[str, str]:
    p1 = points[0] if points else "关键观点"
    p2 = points[1] if len(points) > 1 else "趋势判断"
    p3 = points[2] if len(points) > 2 else "落地方法"

    prompts = {
        "cover": f"为文章《{title}》设计封面图，中文科技评论风，16:9，主标题醒目，信息密度高，留出标题区域。",
        "figure_1": f"围绕观点‘{p1}’生成信息图，中文可读，风格克制专业，竖版，包含标题和三条要点。",
        "figure_2": f"围绕观点‘{p2}’生成对比图，中文可读，包含现象-原因-影响三栏结构。",
    }
    if INLINE_FIGURES >= 3:
        prompts["figure_3"] = f"围绕观点‘{p3}’生成执行路径图，中文可读，包含步骤和风险提示。"
    return prompts


def _extract_json_block(text: str) -> Dict[str, Any]:
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    m = re.search(r"```json\\s*(\\{[\\s\\S]*?\\})\\s*```", text)
    if m:
        return json.loads(m.group(1))

    m2 = re.search(r"(\\{[\\s\\S]*\\})", text)
    if m2:
        return json.loads(m2.group(1))

    raise ValueError("no json object found in llm response")


def llm_generate_article(
    title: str,
    source_url: str,
    source_md: str,
    points: List[str],
    quotes: List[str],
    chapters: List[str],
) -> Dict[str, Any]:
    if chat_generate is None:
        raise RuntimeError("nano_api unavailable")
    if not os.environ.get("NANO_API_KEY", "").strip():
        raise RuntimeError("NANO_API_KEY not set")

    source_excerpt = source_md[:12000]
    prompt = f"""
请基于以下素材，生成一篇可直接发布的中文长文解读（Markdown），风格参考“科技深度解读公众号”，不要空话。

要求：
1) 正文至少 2200 字中文（不是字符数很短的提纲）。
2) 结构必须包含：
   - 标题
   - 导语
   - 要点速览（5-8条）
   - 深度解读（至少5个小节，每节不少于220字）
   - 反方视角/争议点
   - 给创作者的行动建议（至少6条）
   - 结语
   - 原始来源链接
3) 在文中自然插入图片位，使用这些固定标记（调试阶段总图数<=3）：
   ![封面图](images/cover.png)
   ![配图1](images/figure-1.png)
   ![配图2](images/figure-2.png)
4) 不要编造具体事实；不确定时用“基于素材可推断/值得进一步验证”表述。
5) 输出必须是 JSON（不要附带解释），格式：
{{
  "article_markdown": "...",
  "image_prompts": {{
    "cover": "...",
    "figure_1": "...",
    "figure_2": "..."
  }}
}}

素材标题：{title}
素材链接：{source_url}

已提取要点：
{json.dumps(points, ensure_ascii=False)}

已提取证据句：
{json.dumps(quotes, ensure_ascii=False)}

章节线索：
{json.dumps(chapters, ensure_ascii=False)}

原始内容节选：
{source_excerpt}
""".strip()

    raw = chat_generate(prompt=prompt, system="你是严谨的中文科技专栏作者。", max_tokens=6000)
    parsed = _extract_json_block(raw)
    article = (parsed.get("article_markdown") or "").strip()
    prompts = parsed.get("image_prompts") or {}
    if not article:
        raise RuntimeError("llm article_markdown empty")
    return {"article_markdown": article, "image_prompts": prompts}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--analysis", required=True)
    p.add_argument("--meta", required=True)
    p.add_argument("--source", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--image-prompts", required=True)
    args = p.parse_args()

    analysis = read_json(Path(args.analysis))
    meta = read_json(Path(args.meta))
    source_md = read_text(Path(args.source))

    title = maybe_translate_to_zh(meta.get("title", "Untitled"))
    source_url = meta.get("source_url", "")
    points = pick_points(analysis)
    quotes = pick_quotes(analysis)
    chapters = extract_chapters(source_md)
    prompts: Dict[str, Any]

    used_llm = False
    try:
        llm_data = llm_generate_article(
            title=title,
            source_url=source_url,
            source_md=source_md,
            points=points,
            quotes=quotes,
            chapters=chapters,
        )
        article = llm_data["article_markdown"].strip() + "\n"
        prompts = llm_data["image_prompts"] or build_image_prompts(title=title, points=points)
        used_llm = True
    except Exception:
        article = build_article(title=title, points=points, quotes=quotes, chapters=chapters, source_url=source_url)
        prompts = build_image_prompts(title=title, points=points)

    Path(args.output).write_text(article, encoding="utf-8")
    Path(args.image_prompts).write_text(json.dumps(prompts, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {"status": "ok", "output": args.output, "image_prompts": args.image_prompts, "used_llm": used_llm},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
