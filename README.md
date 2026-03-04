# Baoyu Content Pipeline (n8n-ready MVP)

A file-first pipeline for repurposing blogs, long-form articles, and video transcripts.

## Structure

- `scripts/pipeline.py`: stage CLI (`ingest`, `analyze`, `produce`, `publish`, `run`)
- `examples/`: example source/meta/brief inputs
- `output/<run-id>/`: generated stage artifacts
- `prompts/`: reserved for future prompt templates
- `skills/baoyu-content-pipeline/SKILL.md`: reusable skill instructions

## Data Contracts

1. `source.md`: cleaned markdown source text
2. `meta.json`: content metadata
3. `brief.json`: target audience and output goal

### meta.json

```json
{
  "title": "...",
  "source_type": "blog|article|video",
  "source_url": "https://...",
  "author": "...",
  "language": "zh|en",
  "published_at": "YYYY-MM-DD"
}
```

### brief.json

```json
{
  "audience": "...",
  "tone": "...",
  "output_type": "interpretation|infographic|xhs",
  "target_channel": "wechat|x|none"
}
```

## Run

```bash
cd /Users/abelzhou/Desktop/Baoyu
python3 scripts/pipeline.py run \
  --workspace . \
  --source examples/source.example.md \
  --meta examples/meta.example.json \
  --brief examples/brief.example.json
```

## One-Click (macOS)

Double-click this file in Finder:

- `/Users/abelzhou/Desktop/Baoyu/一键图文解读.command`

It will ask for URL and options, then automatically generate:

- `draft.md` (text interpretation)
- `cover.png` or `infographic.png` (image)

## Stage by Stage

```bash
python3 scripts/pipeline.py ingest  --workspace . --source examples/source.example.md --meta examples/meta.example.json --brief examples/brief.example.json --run-id demo001
python3 scripts/pipeline.py analyze --workspace . --source examples/source.example.md --meta examples/meta.example.json --brief examples/brief.example.json --run-id demo001
python3 scripts/pipeline.py produce --workspace . --source examples/source.example.md --meta examples/meta.example.json --brief examples/brief.example.json --run-id demo001
python3 scripts/pipeline.py publish --workspace . --source examples/source.example.md --meta examples/meta.example.json --brief examples/brief.example.json --run-id demo001
```

## n8n Integration

Use `Execute Command` node and parse JSON stdout.

Recommended command for all-in-one run:

```bash
python3 /Users/abelzhou/Desktop/Baoyu/scripts/pipeline.py run \
  --workspace /Users/abelzhou/Desktop/Baoyu \
  --source /Users/abelzhou/Desktop/Baoyu/examples/source.example.md \
  --meta /Users/abelzhou/Desktop/Baoyu/examples/meta.example.json \
  --brief /Users/abelzhou/Desktop/Baoyu/examples/brief.example.json \
  --run-id {{$now.format('yyyyLLdd-HHmmss')}}
```

Recommended n8n flow:

1. Trigger (Manual/Cron/Webhook)
2. Fetch content source (URL or transcript)
3. Save source/meta/brief files
4. Execute Command (`pipeline.py run`)
5. IF node by `status`
6. On success, pass `output/<run-id>/draft.md` to downstream publishing skill

## Quality Gates (built-in)

- source length >= 150 words
- key points >= 3
- evidence quotes >= 2
- summary length >= 40 words

If gate fails, command exits non-zero and returns `{"status":"failed", ...}`.

## Next step with Baoyu skills

- Source capture: `baoyu-url-to-markdown`
- Draft polish: `baoyu-format-markdown`
- Visual outputs: `baoyu-infographic` or `baoyu-xhs-images`
- Publishing: `baoyu-post-to-wechat` or `baoyu-post-to-x`
