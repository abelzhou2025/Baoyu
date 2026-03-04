#!/usr/bin/env python3
"""Baoyu content pipeline.

A stage-based, file-first pipeline for content repurposing:
- ingest: collect source markdown + metadata
- analyze: produce structured analysis json
- produce: generate platform-specific drafts
- publish: emit publish payloads for downstream tools

Designed for local CLI and n8n Execute Command node.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


SUPPORTED_SOURCE_TYPES = {"blog", "article", "video"}
SUPPORTED_OUTPUT_TYPES = {"interpretation", "infographic", "xhs"}
SUPPORTED_CHANNELS = {"wechat", "x", "none"}


@dataclass
class PipelineContext:
    workspace: Path
    run_dir: Path
    source_path: Path
    meta_path: Path
    brief_path: Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _word_count(text: str) -> int:
    latin_tokens = re.findall(r"[A-Za-z0-9_]+", text)
    cjk_chars = re.findall(r"[\u4e00-\u9fff]", text)
    # Count latin tokens and CJK characters together for mixed-language content.
    return len(latin_tokens) + len(cjk_chars)


def _split_sections(markdown_text: str) -> List[Tuple[str, str]]:
    lines = markdown_text.splitlines()
    sections: List[Tuple[str, str]] = []

    current_title = "Introduction"
    current_body: List[str] = []

    for line in lines:
        if line.startswith("#"):
            if current_body:
                sections.append((current_title, "\n".join(current_body).strip()))
            current_title = line.lstrip("#").strip() or "Untitled"
            current_body = []
        else:
            current_body.append(line)

    if current_body:
        sections.append((current_title, "\n".join(current_body).strip()))

    return [(title, body) for title, body in sections if body]


def _clean_line_text(text: str) -> str:
    # Strip markdown links and extra spaces for downstream extraction.
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _extract_candidate_points(markdown_text: str) -> List[str]:
    candidates: List[str] = []
    seen = set()

    for raw in markdown_text.splitlines():
        line = _clean_line_text(raw)
        if not line:
            continue
        if line.startswith("#"):
            continue

        # YouTube chapter-like lines: [00:00] Topic text
        m = re.match(r"^\[\d{1,2}:\d{2}(?::\d{2})?\]\s*(.+)$", line)
        if m:
            point = m.group(1).strip()
            if point and point not in seen:
                seen.add(point)
                candidates.append(point)
            continue

        # Bullet-rich descriptions often use dot separators.
        if "•" in line:
            for part in [p.strip() for p in line.split("•") if p.strip()]:
                if len(part) >= 12 and part not in seen:
                    seen.add(part)
                    candidates.append(part)
            continue

    # Fallback to sentence segmentation.
    text = _clean_line_text(markdown_text)
    for sent in re.split(r"(?<=[.!?。！？])\s+", text):
        sent = sent.strip()
        if len(sent) < 20:
            continue
        if sent not in seen:
            seen.add(sent)
            candidates.append(sent)
        if len(candidates) >= 12:
            break

    return candidates


def _validate_meta(meta: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    required = ["title", "source_type", "source_url", "author", "language"]
    for field in required:
        if not meta.get(field):
            errs.append(f"meta missing required field: {field}")

    source_type = meta.get("source_type")
    if source_type and source_type not in SUPPORTED_SOURCE_TYPES:
        errs.append(
            f"meta.source_type must be one of {sorted(SUPPORTED_SOURCE_TYPES)}, got: {source_type}"
        )
    return errs


def _validate_brief(brief: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    required = ["audience", "tone", "output_type", "target_channel"]
    for field in required:
        if not brief.get(field):
            errs.append(f"brief missing required field: {field}")

    output_type = brief.get("output_type")
    if output_type and output_type not in SUPPORTED_OUTPUT_TYPES:
        errs.append(
            f"brief.output_type must be one of {sorted(SUPPORTED_OUTPUT_TYPES)}, got: {output_type}"
        )

    channel = brief.get("target_channel")
    if channel and channel not in SUPPORTED_CHANNELS:
        errs.append(
            f"brief.target_channel must be one of {sorted(SUPPORTED_CHANNELS)}, got: {channel}"
        )
    return errs


def _quality_gates(source_text: str, analysis: Dict[str, Any]) -> List[str]:
    errs: List[str] = []

    if _word_count(source_text) < 150:
        errs.append("source text too short (<150 words)")

    if len(analysis.get("key_points", [])) < 3:
        errs.append("analysis has fewer than 3 key points")

    if len(analysis.get("evidence_quotes", [])) < 2:
        errs.append("analysis has fewer than 2 evidence quotes")

    if analysis.get("summary") and _word_count(analysis["summary"]) < 40:
        errs.append("analysis summary too short (<40 words)")

    return errs


def build_context(args: argparse.Namespace) -> PipelineContext:
    workspace = Path(args.workspace).resolve()
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = workspace / "output" / run_id

    return PipelineContext(
        workspace=workspace,
        run_dir=run_dir,
        source_path=Path(args.source).resolve(),
        meta_path=Path(args.meta).resolve(),
        brief_path=Path(args.brief).resolve(),
    )


def cmd_ingest(ctx: PipelineContext) -> Dict[str, Any]:
    source_text = _read_text(ctx.source_path).strip()
    meta = _read_json(ctx.meta_path)
    brief = _read_json(ctx.brief_path)

    errors = _validate_meta(meta) + _validate_brief(brief)
    if not source_text:
        errors.append("source markdown is empty")

    ingest = {
        "stage": "ingest",
        "created_at": _now_iso(),
        "source": {
            "path": str(ctx.source_path),
            "word_count": _word_count(source_text),
            "line_count": len(source_text.splitlines()),
        },
        "meta": meta,
        "brief": brief,
        "status": "ok" if not errors else "failed",
        "errors": errors,
    }

    _write_json(ctx.run_dir / "ingest.json", ingest)
    _write_text(ctx.run_dir / "source.md", source_text)
    return ingest


def cmd_analyze(ctx: PipelineContext) -> Dict[str, Any]:
    source_text = _read_text(ctx.run_dir / "source.md")
    ingest = _read_json(ctx.run_dir / "ingest.json")

    sections = _split_sections(source_text)
    section_titles = [title for title, _ in sections[:8]]

    key_points = []
    for title, body in sections[:6]:
        first_sentence = re.split(r"(?<=[.!?。！？])\s+", body.strip())[0][:220].strip()
        if first_sentence:
            key_points.append({"section": title, "point": first_sentence})

    if len(key_points) < 3:
        fallback_points = _extract_candidate_points(source_text)
        for item in fallback_points:
            if len(key_points) >= 6:
                break
            key_points.append({"section": "Highlights", "point": item[:220]})

    paragraphs = [p.strip() for p in source_text.split("\n\n") if p.strip()]
    evidence_quotes = []
    for p in paragraphs:
        if len(evidence_quotes) >= 3:
            break
        clean = _clean_line_text(p)
        if clean.startswith("#"):
            continue
        if len(clean) >= 60:
            evidence_quotes.append(clean[:160])

    if len(evidence_quotes) < 2:
        for item in _extract_candidate_points(source_text):
            quote = item[:160]
            if len(quote) < 40:
                continue
            if quote not in evidence_quotes:
                evidence_quotes.append(quote)
            if len(evidence_quotes) >= 3:
                break

    summary_parts = [item["point"] for item in key_points[:5]]
    summary = " ".join(summary_parts)
    if _word_count(summary) < 40:
        summary += (
            " 本文的核心价值在于把复杂观点拆解为可执行步骤，"
            "并通过结构化表达提升读者吸收效率与传播效果。"
        )

    analysis = {
        "stage": "analyze",
        "created_at": _now_iso(),
        "title": ingest["meta"]["title"],
        "section_titles": section_titles,
        "key_points": key_points,
        "evidence_quotes": evidence_quotes,
        "summary": summary,
        "risks": [
            "Need manual factual verification for claims with numbers.",
            "If source is transcript, polish spoken language before publish.",
        ],
        "status": "ok",
    }

    gate_errors = _quality_gates(source_text, analysis)
    if gate_errors:
        analysis["status"] = "failed"
        analysis["errors"] = gate_errors

    _write_json(ctx.run_dir / "analysis.json", analysis)
    return analysis


def cmd_produce(ctx: PipelineContext) -> Dict[str, Any]:
    ingest = _read_json(ctx.run_dir / "ingest.json")
    analysis = _read_json(ctx.run_dir / "analysis.json")

    if analysis.get("status") != "ok":
        return {
            "stage": "produce",
            "created_at": _now_iso(),
            "status": "failed",
            "errors": ["analysis stage did not pass quality gates"],
        }

    brief = ingest["brief"]
    output_type = brief["output_type"]
    title = ingest["meta"]["title"]
    audience = brief["audience"]
    tone = brief["tone"]

    key_points_text = "\n".join(
        [f"- {item['point']}" for item in analysis.get("key_points", [])[:5]]
    )

    if output_type == "interpretation":
        content = (
            f"# {title}｜解读稿\n\n"
            f"## 面向读者\n{audience}\n\n"
            f"## 核心观点\n{key_points_text}\n\n"
            f"## 一句话总结\n{analysis.get('summary', '')}\n\n"
            f"## 发布建议\n"
            f"- 语气：{tone}\n"
            f"- 结尾增加行动建议（评论区提问或关注下一篇）\n"
        )
    elif output_type == "infographic":
        content = (
            f"# {title}｜信息图脚本\n\n"
            "## 结构建议\n"
            "1. 开场问题\n"
            "2. 关键结论 x3\n"
            "3. 方法/框架\n"
            "4. 落地行动\n\n"
            "## 可视化要点\n"
            f"{key_points_text}\n"
        )
    else:
        content = (
            f"# {title}｜小红书图组脚本\n\n"
            "## 页数建议\n"
            "- 1 封面\n"
            "- 2-6 观点分解\n"
            "- 7 总结 + CTA\n\n"
            "## 每页文案草案\n"
            f"{key_points_text}\n"
        )

    produced = {
        "stage": "produce",
        "created_at": _now_iso(),
        "status": "ok",
        "output_type": output_type,
        "draft_markdown": str(ctx.run_dir / "draft.md"),
        "next_skill_hint": _next_skill_hint(output_type),
    }

    _write_text(ctx.run_dir / "draft.md", content)
    _write_json(ctx.run_dir / "produce.json", produced)
    return produced


def _next_skill_hint(output_type: str) -> str:
    mapping = {
        "interpretation": "Use baoyu-format-markdown, then optionally baoyu-cover-image.",
        "infographic": "Use baoyu-infographic to generate final infographic images.",
        "xhs": "Use baoyu-xhs-images to generate Xiaohongshu image set.",
    }
    return mapping.get(output_type, "No hint")


def cmd_publish(ctx: PipelineContext) -> Dict[str, Any]:
    ingest = _read_json(ctx.run_dir / "ingest.json")
    produce = _read_json(ctx.run_dir / "produce.json")

    if produce.get("status") != "ok":
        result = {
            "stage": "publish",
            "created_at": _now_iso(),
            "status": "failed",
            "errors": ["produce stage failed"],
        }
        _write_json(ctx.run_dir / "publish.json", result)
        return result

    channel = ingest["brief"]["target_channel"]
    payload = {
        "title": ingest["meta"]["title"],
        "channel": channel,
        "content_markdown_path": str(ctx.run_dir / "draft.md"),
    }

    if channel == "wechat":
        payload["recommended_skill"] = "baoyu-post-to-wechat"
    elif channel == "x":
        payload["recommended_skill"] = "baoyu-post-to-x"
    else:
        payload["recommended_skill"] = "none"

    result = {
        "stage": "publish",
        "created_at": _now_iso(),
        "status": "ok",
        "payload": payload,
    }

    _write_json(ctx.run_dir / "publish.json", result)
    return result


def cmd_run_all(ctx: PipelineContext) -> Dict[str, Any]:
    ingest = cmd_ingest(ctx)
    if ingest["status"] != "ok":
        return {"status": "failed", "failed_stage": "ingest", "errors": ingest["errors"]}

    analysis = cmd_analyze(ctx)
    if analysis["status"] != "ok":
        return {
            "status": "failed",
            "failed_stage": "analyze",
            "errors": analysis.get("errors", ["analyze failed"]),
        }

    produce = cmd_produce(ctx)
    if produce["status"] != "ok":
        return {
            "status": "failed",
            "failed_stage": "produce",
            "errors": produce.get("errors", ["produce failed"]),
        }

    publish = cmd_publish(ctx)
    if publish["status"] != "ok":
        return {
            "status": "failed",
            "failed_stage": "publish",
            "errors": publish.get("errors", ["publish failed"]),
        }

    done = {
        "status": "ok",
        "run_dir": str(ctx.run_dir),
        "artifacts": [
            str(ctx.run_dir / "ingest.json"),
            str(ctx.run_dir / "analysis.json"),
            str(ctx.run_dir / "draft.md"),
            str(ctx.run_dir / "produce.json"),
            str(ctx.run_dir / "publish.json"),
        ],
    }
    _write_json(ctx.run_dir / "run.json", done)
    return done


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Baoyu content pipeline")
    p.add_argument("stage", choices=["ingest", "analyze", "produce", "publish", "run"])
    p.add_argument("--workspace", default=".", help="workspace path")
    p.add_argument("--source", required=True, help="source markdown file")
    p.add_argument("--meta", required=True, help="meta json file")
    p.add_argument("--brief", required=True, help="brief json file")
    p.add_argument("--run-id", help="optional run id; default timestamp")
    return p


def main() -> int:
    args = parser().parse_args()
    ctx = build_context(args)

    try:
        if args.stage == "ingest":
            result = cmd_ingest(ctx)
        elif args.stage == "analyze":
            result = cmd_analyze(ctx)
        elif args.stage == "produce":
            result = cmd_produce(ctx)
        elif args.stage == "publish":
            result = cmd_publish(ctx)
        else:
            result = cmd_run_all(ctx)

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "ok" else 2
    except FileNotFoundError as e:
        print(json.dumps({"status": "failed", "error": f"file not found: {e}"}, ensure_ascii=False))
        return 2
    except Exception as e:
        print(json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
