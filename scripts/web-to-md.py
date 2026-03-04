#!/usr/bin/env python3
"""网页转 Markdown 脚本（基础版）"""
import argparse
import sys
from pathlib import Path
import requests
import trafilatura
import urllib3

urllib3.disable_warnings()

def fetch_page(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=15, verify=False)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except Exception as e:
        print(f"❌ 获取网页失败: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="网页转Markdown")
    parser.add_argument("url", help="网页链接")
    parser.add_argument("-o", "--output", help="输出目录")
    parser.add_argument("-f", "--filename", help="文件名")
    args = parser.parse_args()
    
    print(f"📥 正在获取: {args.url}")
    html = fetch_page(args.url)
    
    print("🔍 正在解析内容...")
    # 提取元数据
    metadata = trafilatura.extract_metadata(html, default_url=args.url)

    # 提取内容（不带元数据）
    content = trafilatura.extract(html, url=args.url, include_comments=False,
                                  include_tables=True, output_format="markdown",
                                  with_metadata=False)

    if not content:
        print("❌ 未能提取到有效内容")
        sys.exit(1)

    # 构建输出：YAML frontmatter + H1标题 + 内容
    title = metadata.title if metadata and metadata.title else "Untitled"

    output_lines = []
    output_lines.append("---")
    if metadata:
        if metadata.title:
            output_lines.append(f"title: {metadata.title}")
        if metadata.author:
            output_lines.append(f"author: {metadata.author}")
    output_lines.append(f"url: {args.url}")
    output_lines.append("---")
    output_lines.append("")
    output_lines.append(f"# {title}")
    output_lines.append("")
    output_lines.append(content)

    final_content = "\n".join(output_lines)
    
    filename = args.filename if args.filename else "output.md"
    if not filename.endswith(".md"):
        filename += ".md"
    
    if args.output:
        output_path = Path(args.output)
        output_path.mkdir(parents=True, exist_ok=True)
        file_path = output_path / filename
    else:
        file_path = Path(filename)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(final_content)
    
    print(f"✅ 已保存到: {file_path.absolute()}")

if __name__ == "__main__":
    main()
