#!/usr/bin/env python3
"""Single-file pipeline: URL -> one final markdown with inline images."""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from nano_api import chat_generate, image_generate

WORKSPACE = Path("/Users/abelzhou/Desktop/Baoyu")
URL_TO_MD_SCRIPT = Path("/Users/abelzhou/.agents/skills/baoyu-url-to-markdown/scripts/main.ts")
GEMINI_SCRIPT = Path("/Users/abelzhou/.agents/skills/baoyu-danger-gemini-web/scripts/main.ts")


def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout, p.stderr


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_md_links(text: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)


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

    fm = {}
    for line in lines[1:end_idx]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip().lower()] = v.strip().strip('"').strip("'")

    body = "\n".join(lines[end_idx + 1 :]).strip()
    return fm, body


def detect_source_type(url: str, body: str) -> str:
    u = url.lower()
    if any(x in u for x in ["youtube.com", "youtu.be", "bilibili.com", "vimeo.com"]):
        return "video"
    if re.search(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", body):
        return "video"
    words = len(re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", body))
    return "article" if words > 2200 else "blog"


def guess_title(front: Dict[str, str], body: str) -> str:
    for k in ["title", "og:title"]:
        if front.get(k):
            return front[k]
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "未命名内容"


def zh_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def latin_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]", text))


def extract_json_block(text: str) -> Dict:
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        return json.loads(t)
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", t)
    if m:
        return json.loads(m.group(1))
    m2 = re.search(r"(\{[\s\S]*\})", t)
    if m2:
        return json.loads(m2.group(1))
    raise ValueError("no json in model output")


def _extract_last_json_obj(text: str) -> Dict:
    m = re.search(r"(\{[\s\S]*\})\s*$", text.strip())
    if not m:
        raise ValueError("no trailing json object")
    return json.loads(m.group(1))


def has_nano_key() -> bool:
    return bool(os.environ.get("NANO_API_KEY", "").strip())


def gemini_web_generate_text(prompt: str, max_tries: int = 2) -> str:
    for _ in range(max_tries):
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as f:
            f.write(prompt)
            prompt_file = f.name
        try:
            code, out, err = run_cmd(
                [
                    "npx",
                    "-y",
                    "bun",
                    str(GEMINI_SCRIPT),
                    "--promptfiles",
                    prompt_file,
                    "--json",
                    "--model",
                    "gemini-2.5-pro",
                ]
            )
            if code != 0:
                continue
            data = _extract_last_json_obj(out)
            text = (data.get("text") or "").strip()
            if text:
                return text
        except Exception:
            continue
        finally:
            try:
                Path(prompt_file).unlink(missing_ok=True)
            except Exception:
                pass
    raise RuntimeError("gemini web text generation failed")


def llm_generate(prompt: str, system: str, max_tokens: int) -> str:
    if has_nano_key():
        return chat_generate(prompt=prompt, system=system, max_tokens=max_tokens)
    return gemini_web_generate_text(prompt=prompt)


def normalize_image_count(n: int) -> int:
    return max(0, min(4, n))


def image_keys(image_count: int) -> List[str]:
    c = normalize_image_count(image_count)
    keys: List[str] = []
    if c >= 1:
        keys.append("cover")
    for i in range(1, c):
        keys.append(f"figure_{i}")
    return keys


def notebooklm_keys(deck_count: int) -> List[str]:
    c = max(0, min(3, deck_count))
    return [f"deck_{i}" for i in range(1, c + 1)]


def all_image_keys(image_count: int, deck_count: int) -> List[str]:
    return image_keys(image_count) + notebooklm_keys(deck_count)


def rel_path_for_key(key: str) -> str:
    if key == "cover":
        return "images/cover.png"
    if key.startswith("figure_"):
        idx = key.split("_")[1]
        return f"images/figure-{idx}.png"
    if key.startswith("deck_"):
        idx = key.split("_")[1]
        return f"images/deck-{idx}.png"
    return f"images/{key}.png"


def build_image_markers(image_count: int) -> str:
    keys = image_keys(image_count)
    lines: List[str] = []
    for k in keys:
        if k == "cover":
            lines.append(f"![封面图]({rel_path_for_key(k)})")
        else:
            i = int(k.split("_")[1])
            lines.append(f"![配图{i}]({rel_path_for_key(k)})")
    return "\n".join(lines)


def build_default_image_prompts(title: str, image_count: int, image_style: str) -> Dict[str, str]:
    return build_default_image_prompts_from_article(
        title=title,
        article="",
        image_count=image_count,
        image_style=image_style,
    )


def _extract_para_topics(article: str, max_topics: int) -> List[str]:
    text = strip_md_links(article)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    candidates: List[str] = []
    for b in blocks:
        if b.startswith("#"):
            continue
        if re.search(r"^\s*!\[.*\]\(.*\)\s*$", b):
            continue
        if any(x in b for x in ["来源链接", "原文链接", "参考链接"]):
            continue
        s = re.sub(r"\s+", " ", b).strip()
        if len(s) < 40:
            continue
        candidates.append(s[:100])
    if not candidates:
        return []
    n = min(max_topics, len(candidates))
    return random.sample(candidates, n)


def _summarize_article_topic(title: str, article: str) -> str:
    text = strip_md_links(article)
    text = re.sub(r"(?m)^\s*#+\s*", "", text)
    text = re.sub(r"(?m)^\s*!\[.*\]\(.*\)\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return f"《{title}》的核心观点与关键论证"
    core = text[:220]
    return f"《{title}》的整体核心：{core}"


def build_default_image_prompts_from_article(
    title: str, article: str, image_count: int, image_style: str
) -> Dict[str, str]:
    keys = image_keys(image_count)
    prompts: Dict[str, str] = {}
    style_map = {
        "cartoon": "卡通插画风，色彩鲜明，人物与元素简洁",
        "realistic": "偏写实杂志风，质感清晰，构图专业",
        "infographic": "信息图风格，结构清晰，重点突出",
        "minimal": "极简风，留白充足，版式克制",
        "pencil-doodle": (
            "彩色铅笔涂鸦，保留纸张颗粒和铅笔笔触，色彩明快，细线手绘，童话感，非写实"
        ),
    }
    style_text = style_map.get(image_style, image_style)
    figure_count = max(0, len(keys) - 1)
    topics = _extract_para_topics(article, max_topics=figure_count)
    while len(topics) < figure_count:
        topics.append(f"《{title}》中的关键观点")
    cover_topic = _summarize_article_topic(title, article)

    neg = (
        "禁止 photorealistic、3D render、cinematic、digital painting、"
        "blurry、dramatic lighting、logo、水印、二维码、无关英文。"
    )

    fig_idx = 0
    for k in keys:
        if k == "cover":
            prompts[k] = (
                f"根据全文概括“{cover_topic}”，为《{title}》生成主图。"
                f"风格：{style_text}。"
                "画幅比例必须为 3:1（超宽横图），主体清晰，留白适中。"
                "请先用一句话总结全文主旨，再把主旨转成画面元素。"
                "画面必须中文可读。"
                + neg
            )
        else:
            i = int(k.split('_')[1])
            topic = topics[fig_idx] if fig_idx < len(topics) else f"《{title}》中的关键观点{i}"
            fig_idx += 1
            prompts[k] = (
                f"根据段落主题“{topic}”，生成第{i}张配图。"
                f"风格：{style_text}。"
                "画幅比例必须为 16:9，画面与段落主题强相关。"
                "请先用一句话概括该段，再把概括转成画面元素。"
                "画面必须中文可读。"
                + neg
            )
    return prompts


def build_notebooklm_markers(deck_count: int) -> str:
    keys = notebooklm_keys(deck_count)
    lines: List[str] = []
    for k in keys:
        i = int(k.split("_")[1])
        lines.append(f"![NotebookLM图卡{i}]({rel_path_for_key(k)})")
    return "\n".join(lines)


def build_notebooklm_prompts(title: str, article: str, deck_count: int, deck_style: str) -> Dict[str, str]:
    keys = notebooklm_keys(deck_count)
    if not keys:
        return {}

    style_map = {
        "clean": "NotebookLM式清爽幻灯片风，白底+蓝灰点缀，信息层次清晰",
        "corporate": "NotebookLM式商务汇报风，专业蓝色系，图文均衡",
        "chalkboard": "NotebookLM式课堂讲解风，示意图和手绘感标注",
        "minimal": "NotebookLM式极简风，大留白+单一核心观点",
    }
    style_text = style_map.get(deck_style, deck_style)

    # Use first few headings as anchors so card topics align with article.
    headings = [m.group(1).strip() for m in re.finditer(r"(?m)^##\s+(.+)$", article)]
    prompts: Dict[str, str] = {}
    for idx, k in enumerate(keys, start=1):
        topic = headings[idx - 1] if idx - 1 < len(headings) else f"关键观点{idx}"
        prompts[k] = (
            f"生成一张 16:9 中文演示页，主题《{title}》的“{topic}”。"
            f"风格：{style_text}。"
            "要求：像高质量 NotebookLM 摘要卡片，包含标题、3-5个要点、简洁图形元素，文字可读。"
            "禁止水印、logo、二维码、无关英文段落。"
        )
    return prompts


def free_translate(text: str) -> str:
    """Stable free translation via Google Translate web endpoint."""
    text = text.strip()
    if not text:
        return text
    if zh_count(text) >= max(20, latin_count(text) // 2):
        return text

    parts: List[str] = []
    chunk_size = 1200
    for i in range(0, len(text), chunk_size):
        chunk = text[i : i + chunk_size]
        url = (
            "https://translate.googleapis.com/translate_a/single"
            "?client=gtx&sl=auto&tl=zh-CN&dt=t&q="
            + urllib.parse.quote(chunk)
        )
        try:
            with urllib.request.urlopen(url, timeout=12) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            translated = "".join(seg[0] for seg in payload[0] if seg and seg[0])
            parts.append(translated.strip() or chunk)
        except Exception:
            parts.append(chunk)
    return "\n".join(parts)


def normalize_markdown_chinese(md: str) -> str:
    lines = md.splitlines()
    out: List[str] = []
    for line in lines:
        striped = line.strip()
        if not striped:
            out.append(line)
            continue
        if striped.startswith("![") and "](data:image" in striped:
            out.append(line)
            continue
        if latin_count(striped) > 50 and zh_count(striped) < 8:
            out.append(free_translate(striped))
        else:
            out.append(line)
    return "\n".join(out).strip() + "\n"


def fetch_youtube_markdown(url: str, out_path: Path) -> bool:
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        return False
    code, sout, _ = run_cmd([ytdlp, "-J", "--skip-download", url])
    if code != 0 or not sout.strip():
        return False
    try:
        data = json.loads(sout)
    except Exception:
        return False

    title = data.get("title") or "Untitled Video"
    uploader = data.get("uploader") or "Unknown"
    upload_date = data.get("upload_date") or ""
    description = data.get("description") or ""
    chapters = data.get("chapters") or []

    lines = [f"# {title}", "", f"- Uploader: {uploader}", f"- URL: {url}"]
    if upload_date:
        lines.append(f"- Upload Date: {upload_date}")
    lines.append("")
    if chapters:
        lines.append("## Chapters")
        for c in chapters[:20]:
            t = c.get("title") or ""
            s = int(c.get("start_time") or 0)
            lines.append(f"- [{s // 60:02d}:{s % 60:02d}] {t}")
        lines.append("")
    lines.append("## Description")
    lines.append(description)
    out_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return True


def fetch_markdown(url: str, out_path: Path) -> str:
    """Try no-browser fetch first to reduce popup chance."""
    if any(x in url.lower() for x in ["youtube.com", "youtu.be"]) and fetch_youtube_markdown(url, out_path):
        return "yt-dlp"

    jina_url = f"https://r.jina.ai/{url}"
    try:
        with urllib.request.urlopen(jina_url, timeout=25) as resp:
            txt = resp.read().decode("utf-8", errors="replace")
        if len(txt) > 800:
            out_path.write_text(txt, encoding="utf-8")
            return "jina"
    except Exception:
        pass

    code, sout, serr = run_cmd(["npx", "-y", "bun", str(URL_TO_MD_SCRIPT), url, "-o", str(out_path)])
    if code != 0 or not out_path.exists():
        raise RuntimeError(f"fetch url failed: {serr or sout}")
    return "browser"


def generate_article(
    title: str,
    url: str,
    source_type: str,
    body: str,
    image_count: int,
    image_style: str,
    style: str,
) -> Tuple[str, Dict[str, str]]:
    source_excerpt = strip_md_links(body)[:15000]
    # Pre-translate excerpt for better Chinese output stability.
    source_excerpt_zh = free_translate(source_excerpt)

    marker_block = build_image_markers(image_count)
    prompt = f"""
你是一位中文深度解读作者，请把原始素材改写成面向普通中文读者的长篇解读。

目标：
- 字数 1500-2200（中文）
- 让读者看完就理解：原文到底说了什么、论证是否站得住、对实践有什么影响

写作要求：
1) 风格：纯叙述、解释充分、口语化但不口水
2) 结构：开场背景 -> 要点速览 -> 4-6 个深度段落 -> 收束思考
3) 每个深度段落都要有：事实/原话要点 + 你的解释 + 对读者的启发
4) 视频内容按讨论推进顺序讲，不要机械时间戳清单；博客/长文按主题推进，不写“时间线”
5) 删除广告和无关信息（订阅、引流、标签串、寒暄）
6) 严禁出现这些模板词或句式：
   - 核心判读决策
   - 行动清单
   - 解释与启示（作为小标题）
   - 下面这篇是对原链接内容的中文化解读
   - 阅读这类内容时，建议把“观点热度”和“证据质量”分开看
7) 不编造事实，不确定处写“可推断但需验证”
8) 保留并明确给出来源链接（原始URL）
9) 若提供图片位，请在正文自然插入这些标记（原样保留）：
{marker_block if marker_block else '(本次不需要图片)'}

输出严格 JSON：
{{
  "article_markdown": "...",
  "image_prompts": {{
    "cover": "...",
    "figure_1": "...",
    "figure_2": "..."
  }}
}}

标题：{title}
类型：{source_type}
链接：{url}
素材（中文化后）：
{source_excerpt_zh}
""".strip()

    raw = llm_generate(prompt=prompt, system="你是资深中文科技作者", max_tokens=7000)
    try:
        data = extract_json_block(raw)
        article = (data.get("article_markdown") or "").strip()
    except Exception:
        article = raw.strip()

    if zh_count(article) < 1300:
        expand_prompt = (
            "将下文扩写到 1500-2200 字，增加论证细节与例子，不要引入无关套话，保留图片位与来源链接：\n\n"
            + article
        )
        article = llm_generate(prompt=expand_prompt, system="你是资深中文科技作者", max_tokens=6500)

    article = scrub_boilerplate(article)
    article = normalize_markdown_chinese(article)
    if zh_count(article) < 1000:
        raise RuntimeError("article too short after retries")

    prompts = build_default_image_prompts_from_article(
        title=title,
        article=article,
        image_count=image_count,
        image_style=image_style,
    )
    keys = image_keys(image_count)
    return article, {k: prompts[k] for k in keys if k in prompts}


def scrub_boilerplate(md: str) -> str:
    banned_snippets = [
        "下面这篇是对原链接内容的中文化解读",
        "阅读这类内容时，建议把“观点热度”和“证据质量”分开看",
        "核心判读决策",
        "行动清单",
        "解释与启示：",
        "解释与启示:",
    ]
    out = md
    for s in banned_snippets:
        out = out.replace(s, "")

    banned_line_patterns = [
        r"^\s*核心判读决策\s*$",
        r"^\s*行动清单\s*$",
        r"^\s*解释与启示[:：]?\s*$",
        r"^\s*订阅.*$",
        r"^\s*关注.*$",
    ]
    lines = []
    for line in out.splitlines():
        if any(re.search(p, line) for p in banned_line_patterns):
            continue
        lines.append(line)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"
    return out


def ensure_source_link(md: str, url: str) -> str:
    text = md.strip()
    if url in text:
        return text + "\n"
    if re.search(r"(?mi)^##\s*(来源链接|原文链接|参考链接)\s*$", text):
        return text + f"\n- {url}\n"
    return text + f"\n\n## 来源链接\n- {url}\n"


def fallback_article(title: str, url: str, body: str, image_count: int, image_style: str) -> Tuple[str, Dict[str, str]]:
    raw = strip_md_links(body)[:14000]
    lowered = raw.lower()
    start = lowered.find("## description")
    if start != -1:
        raw = raw[start + len("## description") :]

    # Extract chapters for optional chronological summary.
    chapters = []
    for line in raw.splitlines():
        m = re.search(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.+)", line)
        if m:
            chapters.append((m.group(1), free_translate(m.group(2).strip())))
        if len(chapters) >= 10:
            break

    noise_patterns = [
        r"(?i)don't forget to subscribe.*",
        r"(?i)subscribe to.*",
        r"(?i)follow.*",
        r"(?i)linkedin.*",
        r"(?i)chapters?:.*",
        r"(?m)^\s*\d{1,2}:\d{2}(:\d{2})?.*$",
        r"(?i)#\w+",
        r"https?://\S+",
    ]
    for pat in noise_patterns:
        raw = re.sub(pat, "", raw, flags=re.MULTILINE)

    clean = free_translate(raw)
    clean = re.sub(r"[•▪◦]", "。", clean)
    clean = re.sub(r"\s+", " ", clean)
    sentences = [s.strip() for s in re.split(r"(?<=[。！？.!?])\s+", clean) if len(s.strip()) > 18]

    # Deduplicate while preserving order.
    uniq: List[str] = []
    seen = set()
    skip_keys = ["在本集中您将学到", "章节", "上传者", "上传日期", "主持人", "嘉宾"]
    for s in sentences:
        if any(k in s for k in skip_keys):
            continue
        k = re.sub(r"\s+", "", s)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(s)
        if len(uniq) >= 90:
            break

    if not uniq:
        uniq = ["素材信息较少，建议补充更完整正文后再生成。"]

    def take_range(a: int, b: int, fb_a: int, fb_b: int) -> str:
        x = " ".join(uniq[a:b]).strip()
        if x:
            return x
        return " ".join(uniq[fb_a:fb_b]).strip()

    p0 = take_range(0, 8, 0, 6)
    p1 = take_range(8, 20, 2, 12)
    p2 = take_range(20, 34, 6, 16)
    p3 = take_range(34, 50, 10, 22)
    p4 = take_range(50, 68, 14, 26)
    p5 = take_range(68, 86, 18, 32)

    chrono = ""
    if chapters:
        chrono_lines = ["按讨论推进顺序看，内容大致从以下几个问题逐步展开："]
        for t, ch in chapters[:8]:
            chrono_lines.append(f"{ch}。")
        chrono = "\n".join(chrono_lines) + "\n"

    marker_block = build_image_markers(image_count)
    marker_lines = marker_block.splitlines() if marker_block else []
    cover_line = marker_lines[0] if marker_lines else ""
    fig1_line = marker_lines[1] if len(marker_lines) > 1 else ""
    fig2_line = marker_lines[2] if len(marker_lines) > 2 else ""

    md = f"""# {free_translate(title)}｜深度解读

{cover_line}

这篇内容的核心不在于“观点有多新”，而在于它如何组织事实、如何推导结论，以及这些结论在现实场景里到底有没有可迁移性。下面按内容推进顺序，结合原文信息做中文解读。

## 要点速览
{p0}

## 深度展开（一）
{p1}

{fig1_line}

## 深度展开（二）
{p2}

## 深度展开（三）
{p3}

{fig2_line}

## 深度展开（四）
{p4}

## 延伸讨论
{p5}

{chrono}
## 原文链接
{url}
"""
    md = scrub_boilerplate(md)
    md = normalize_markdown_chinese(md)

    prompts = build_default_image_prompts_from_article(
        title=free_translate(title),
        article=md,
        image_count=image_count,
        image_style=image_style,
    )
    return md, prompts


def image_with_backend(prompt: str, out: Path, backend: str) -> bool:
    if backend == "api":
        try:
            image_generate(prompt=prompt, out_path=out)
            return True
        except Exception:
            return False

    if backend == "gemini":
        code, _, _ = run_cmd(["npx", "-y", "bun", str(GEMINI_SCRIPT), "--prompt", prompt, "--image", str(out)])
        return code == 0 and out.exists()

    # auto: prefer API, fallback to gemini web.
    try:
        image_generate(prompt=prompt, out_path=out)
        return True
    except Exception:
        code, _, _ = run_cmd(["npx", "-y", "bun", str(GEMINI_SCRIPT), "--prompt", prompt, "--image", str(out)])
        return code == 0 and out.exists()


def image_with_backend_verbose(prompt: str, out: Path, backend: str) -> Tuple[bool, str]:
    if backend == "api":
        try:
            image_generate(prompt=prompt, out_path=out)
            return (out.exists(), "ok")
        except Exception as e:
            return (False, f"api_error: {e}")

    if backend == "gemini":
        code, so, se = run_cmd(["npx", "-y", "bun", str(GEMINI_SCRIPT), "--prompt", prompt, "--image", str(out)])
        if code == 0 and out.exists():
            return (True, "ok")
        msg = (se or so or f"exit={code}").strip().splitlines()[-1] if (se or so) else f"exit={code}"
        return (False, f"gemini_error: {msg}")

    # auto: prefer API, fallback to gemini web.
    try:
        image_generate(prompt=prompt, out_path=out)
        if out.exists():
            return (True, "ok")
    except Exception as e:
        api_msg = f"api_error: {e}"
    else:
        api_msg = "api_error: output_missing"
    code, so, se = run_cmd(["npx", "-y", "bun", str(GEMINI_SCRIPT), "--prompt", prompt, "--image", str(out)])
    if code == 0 and out.exists():
        return (True, "ok")
    gmsg = (se or so or f"exit={code}").strip().splitlines()[-1] if (se or so) else f"exit={code}"
    return (False, f"{api_msg}; gemini_error: {gmsg}")


def generate_image_with_retries(
    key: str,
    prompt: str,
    out: Path,
    backend: str,
    attempts: int = 3,
) -> Tuple[bool, List[str]]:
    logs: List[str] = []
    n = max(1, attempts)
    for i in range(1, n + 1):
        ok, reason = image_with_backend_verbose(prompt=prompt, out=out, backend=backend)
        logs.append(f"{key} attempt {i}/{n}: {reason}")
        if ok and out.exists():
            return True, logs
        if i < n:
            time.sleep(1.5 * (2 ** (i - 1)))
    return False, logs


def _mime_for_path(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if ext == ".webp":
        return "image/webp"
    return "image/png"


def _optimize_for_embed(p: Path, key: str) -> Path:
    if not p.exists():
        return p
    # Prefer aggressive but readable compression for inline markdown embedding.
    max_edge = 2200 if key == "cover" else 1600
    quality = "80" if key == "cover" else "78"
    out = p.parent / f"{p.stem}.embed.jpg"
    code, _, _ = run_cmd(
        [
            "sips",
            "-s",
            "format",
            "jpeg",
            "--setProperty",
            "formatOptions",
            quality,
            "--resampleHeightWidthMax",
            str(max_edge),
            str(p),
            "--out",
            str(out),
        ]
    )
    if code != 0 or not out.exists():
        return p
    try:
        # Use optimized version only when meaningfully smaller.
        if out.stat().st_size < p.stat().st_size * 0.92:
            return out
    except Exception:
        pass
    return p


def to_data_uri(p: Path, key: str) -> str:
    chosen = _optimize_for_embed(p, key)
    b64 = base64.b64encode(chosen.read_bytes()).decode("ascii")
    return f"data:{_mime_for_path(chosen)};base64,{b64}"


def placeholder_data_uri(label: str) -> str:
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='675'>"
        "<rect width='100%' height='100%' fill='#f2f4f8'/>"
        f"<text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' fill='#444' font-size='38'>{label}</text>"
        "</svg>"
    )
    return "data:image/svg+xml;utf8," + urllib.parse.quote(svg)


def _placeholder_by_key(key: str) -> str:
    if key == "cover":
        return placeholder_data_uri("封面图")
    if key.startswith("figure_"):
        idx = key.split("_")[1]
        return placeholder_data_uri(f"配图{idx}")
    if key.startswith("deck_"):
        idx = key.split("_")[1]
        return placeholder_data_uri(f"图卡{idx}")
    return placeholder_data_uri(key)


def embed_images(md: str, imgs: Dict[str, Path]) -> str:
    result = md
    uri_cache: Dict[str, str] = {}
    for key, path in imgs.items():
        rel = rel_path_for_key(key)
        if path.exists():
            uri = uri_cache.setdefault(key, to_data_uri(path, key))
            result = result.replace(f"]({rel})", f"]({uri})")
        else:
            # Remove unresolved image markers instead of keeping placeholders.
            result = re.sub(
                rf"(?m)^\s*!\[[^\]]*\]\(\s*{re.escape(rel)}\s*\)\s*\n?",
                "",
                result,
            )

    for key, path in imgs.items():
        if not path.exists():
            continue
        uri = uri_cache.setdefault(key, to_data_uri(path, key))
        if uri not in result:
            result += f"\n\n![{key}]({uri})\n"
    result = re.sub(r"\n{3,}", "\n\n", result).strip() + "\n"
    return result


def safe_name(title: str) -> str:
    t = re.sub(r"[\\/:*?\"<>|]", "-", title).strip()
    t = re.sub(r"\s+", "-", t)
    return (t[:48] or "内容解读").strip("-")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True)
    p.add_argument("--workspace", default=str(WORKSPACE))
    p.add_argument("--image-backend", choices=["auto", "api", "gemini"], default="auto")
    p.add_argument("--image-count", type=int, default=3)
    p.add_argument("--image-style", default="cartoon")
    p.add_argument("--deck-count", type=int, default=0)
    p.add_argument("--deck-style", choices=["clean", "corporate", "chalkboard", "minimal"], default="clean")
    p.add_argument("--style", choices=["plain", "structured"], default="plain")
    args = p.parse_args()

    workspace = Path(args.workspace).resolve()
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    image_count = normalize_image_count(args.image_count)
    deck_count = max(0, min(3, args.deck_count))

    tmp_dir = Path(tempfile.mkdtemp(prefix="baoyu-final-", dir=str(workspace)))
    fetch_mode = "unknown"
    try:
        source_md = tmp_dir / "source.md"
        fetch_mode = fetch_markdown(args.url, source_md)

        raw = read_text(source_md)
        front, body = parse_frontmatter(raw)
        body = body or raw
        title = guess_title(front, body)
        source_type = detect_source_type(args.url, body)

        try:
            article, prompts = generate_article(
                title,
                args.url,
                source_type,
                body,
                image_count=image_count,
                image_style=args.image_style,
                style=args.style,
            )
            article_mode = "llm"
        except Exception:
            article, prompts = fallback_article(title, args.url, body, image_count=image_count, image_style=args.image_style)
            article_mode = "fallback"

        if deck_count > 0:
            deck_markers = build_notebooklm_markers(deck_count)
            if deck_markers and deck_markers not in article:
                article = article.rstrip() + "\n\n## 可视化图卡（NotebookLM风格）\n" + deck_markers + "\n"

        imgs: Dict[str, Path] = {}
        for k in all_image_keys(image_count, deck_count):
            if k == "cover":
                imgs[k] = tmp_dir / "cover.png"
            elif k.startswith("figure_"):
                idx = k.split("_")[1]
                imgs[k] = tmp_dir / f"figure-{idx}.png"
            elif k.startswith("deck_"):
                idx = k.split("_")[1]
                imgs[k] = tmp_dir / f"deck-{idx}.png"

        deck_prompts = build_notebooklm_prompts(
            title=free_translate(title),
            article=article,
            deck_count=deck_count,
            deck_style=args.deck_style,
        )

        merged_prompts = dict(prompts)
        merged_prompts.update(deck_prompts)

        image_logs: Dict[str, List[str]] = {}
        failed_keys: List[str] = []
        required_keys = all_image_keys(image_count, deck_count)
        for k in required_keys:
            prompt = merged_prompts.get(k, "").strip()
            if not prompt:
                image_logs[k] = [f"{k}: prompt missing"]
                failed_keys.append(k)
                continue
            ok, logs = generate_image_with_retries(
                key=k,
                prompt=prompt,
                out=imgs[k],
                backend=args.image_backend,
                attempts=3,
            )
            image_logs[k] = logs
            if not ok:
                failed_keys.append(k)

        warnings: List[str] = []
        if failed_keys:
            warn_lines = [f"missing images: {', '.join(failed_keys)}"]
            for k in failed_keys:
                for line in image_logs.get(k, []):
                    warn_lines.append(line)
            warnings.append("; ".join(warn_lines))

        final_md = embed_images(article, imgs)
        final_md = ensure_source_link(final_md, args.url)
        out_name = f"{safe_name(free_translate(title))}-{ts}.md"
        out_path = workspace / out_name
        out_path.write_text(final_md, encoding="utf-8")

        print(
            json.dumps(
                {
                    "status": "ok",
                    "source_type": source_type,
                    "output": str(out_path),
                    "title": title,
                    "fetch_mode": fetch_mode,
                    "article_mode": article_mode,
                    "image_backend": args.image_backend,
                    "image_count": image_count,
                    "image_style": args.image_style,
                    "deck_count": deck_count,
                    "deck_style": args.deck_style,
                    "style": args.style,
                    "images": {k: str(v.exists()) for k, v in imgs.items()},
                    "missing_images": failed_keys,
                    "warnings": warnings,
                    "image_attempt_logs": image_logs,
                    "prompts_used": {
                        k: merged_prompts.get(k, "")
                        for k in all_image_keys(image_count, deck_count)
                        if merged_prompts.get(k, "")
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
