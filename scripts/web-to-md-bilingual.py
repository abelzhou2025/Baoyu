#!/usr/bin/env python3
"""
网页转 Markdown 脚本（高级版）
- 图片以 Base64 内嵌
- 每段英文后附带中文翻译
- 图片放在原文位置
"""

import argparse
import base64
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
import trafilatura
from deep_translator import GoogleTranslator
import urllib3
import bs4

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def translate(text):
    return GoogleTranslator(source="en", target="zh-CN").translate(text)

def download_image(img_url, base_url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        full_url = urljoin(base_url, img_url)
        response = requests.get(full_url, headers=headers, timeout=10, verify=False)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "image/jpeg")
        if "png" in content_type.lower():
            img_type = "png"
        elif "gif" in content_type.lower():
            img_type = "gif"
        elif "webp" in content_type.lower():
            img_type = "webp"
        else:
            img_type = "jpg"
        
        img_base64 = base64.b64encode(response.content).decode("utf-8")
        return f"data:image/{img_type};base64,{img_base64}", True
    except Exception as e:
        print(f"⚠️  图片下载失败: {img_url}")
        return img_url, False

def fetch_page(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or "utf-8"
        return response.text
    except Exception as e:
        print(f"❌ 获取网页失败: {e}")
        sys.exit(1)

def extract_content(html, url):
    print("🔍 正在解析内容...")
    
    metadata = trafilatura.metadata.extract_metadata(html)
    metadata_md = "---\n"
    if metadata.title:
        metadata_md += f"title: {metadata.title}\n"
    if metadata.author:
        metadata_md += f"author: {metadata.author}\n"
    metadata_md += f"url: {url}\n"
    if metadata.hostname:
        metadata_md += f"hostname: {metadata.hostname}\n"
    if metadata.description:
        metadata_md += f"description: {metadata.description}\n"
    if metadata.sitename:
        metadata_md += f"sitename: {metadata.sitename}\n"
    if metadata.date:
        metadata_md += f"date: {metadata.date}\n"
    metadata_md += "---\n\n"
    
    main_content = trafilatura.extract(html, url=url, include_comments=False, 
                                       include_tables=True, include_images=False,
                                       output_format="markdown", with_metadata=False)
    
    if not main_content:
        print("❌ 未能提取到有效内容")
        sys.exit(1)
    
    soup = bs4.BeautifulSoup(html, "html.parser")
    images = []
    
    og_image = soup.find("meta", property="og:image")
    if og_image and og_image.get("content"):
        images.append(("Featured Image", og_image["content"], "featured"))
    
    content_lines = main_content.split("\n")
    processed_content = []
    
    for alt, img_url, _ in images:
        print(f"📷 处理封面图...")
        img_data, success = download_image(img_url, url)
        if success:
            processed_content.append(f"![{alt}]({img_data})\n\n")
    
    for line in content_lines:
        processed_content.append(line)
    
    print(f"📷 共处理 {len(images)} 张图片")
    return metadata_md + "\n".join(processed_content)

def add_translations(markdown_content):
    print("🌐 正在翻译内容...")
    lines = markdown_content.split("\n")
    result = []
    skip_yaml = False
    
    for i, line in enumerate(lines):
        result.append(line)
        if line.strip() == "---" and i == 0:
            skip_yaml = True
            continue
        if skip_yaml:
            if line.strip() == "---":
                skip_yaml = False
            continue
        
        stripped = line.strip()
        if (stripped and not stripped.startswith("#") and not stripped.startswith(">") 
            and not stripped.startswith("![") and len(stripped) > 30 
            and not stripped.startswith("**") or ":" in stripped):
            try:
                translation = translate(stripped)
                if translation:
                    result.append(f"> {translation}")
            except:
                pass
    
    return "\n".join(result)

def save_markdown(content, filename, output_dir=None):
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        file_path = output_path / filename
    else:
        file_path = Path(filename)
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    print(f"✅ 已保存到: {file_path.absolute()}")
    return file_path

def main():
    import argparse
    parser = argparse.ArgumentParser(description="网页转中英对照Markdown")
    parser.add_argument("url", help="网页链接")
    parser.add_argument("-o", "--output", help="输出目录")
    parser.add_argument("-f", "--filename", help="文件名")
    args = parser.parse_args()
    
    print(f"📥 正在获取: {args.url}")
    html = fetch_page(args.url)
    content = extract_content(html, args.url)
    content = add_translations(content)
    
    filename = args.filename if args.filename else "output.md"
    if not filename.endswith(".md"):
        filename += ".md"
    
    save_markdown(content, filename, args.output)

if __name__ == "__main__":
    main()
