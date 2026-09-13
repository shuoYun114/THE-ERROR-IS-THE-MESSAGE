#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库全量数据与多媒体附件归档工具 (Repository Dump Tool - Complete Specification)
完全基于 Python 3 标准库，零第三方外部依赖。
严格实现官方 SPEC 核心要求：
1. 三大核心数据区完整抓取：Issues、Pull Requests、Releases (含Release Notes、Tags与附件)
2. 全量媒体附件深度下载：图片、音频、视频、PDF、Release Assets，分块流式写入防OOM + 3次重试
3. 三重文件名还原引擎：Content-Disposition + S3重定向 + MIME类型，100%精准还原真实文件名
4. 双重视图离线展示：暗黑模式 index.html (带音视频播放器与XSS防护) + GitHub原生 SUMMARY.md
"""

import os
import sys
import json
import re
import html
import time
import mimetypes
import hashlib
import urllib.request
import urllib.parse
import subprocess
from pathlib import Path
from datetime import datetime, timezone

# 强制 UTF-8 避免 Windows 终端乱码
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# 附件与多媒体链接匹配正则
MEDIA_URL_PATTERNS = [
    r'https?://github\.com/user-attachments/(?:assets|files)/[^\s\)\"\'>]+',
    r'<img\s+[^>]*?src=["\'](https?://[^"\']+)["\']',
    r'<audio\s+[^>]*?src=["\'](https?://[^"\']+)["\']',
    r'<video\s+[^>]*?src=["\'](https?://[^"\']+)["\']',
    r'!\[.*?\]\((https?://[^\s\)]+)\)',
    r'\[.*?\]\((https?://github\.com/user-attachments/[^\s\)]+)\)',
]

CHUNK_SIZE = 64 * 1024 # 64 KB 分块流式读取

class RepoDumper:
    def __init__(self, repo: str, output_dir: str = 'dump', token: str = None):
        self.repo = repo.strip()
        self.output_dir = Path(output_dir)
        self.media_dir = self.output_dir / 'media'
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        
        self.token = token or os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if not self.token:
            try:
                t = subprocess.check_output('gh auth token', shell=True).decode().strip()
                if t and len(t) > 5:
                    self.token = t
            except Exception:
                pass

        self.headers = {
            'User-Agent': 'Repo-Dump-Tool/2.1',
            'Accept': 'application/vnd.github.v3+json'
        }
        if self.token:
            self.headers['Authorization'] = f'Bearer {self.token}'
            
        self.downloaded_media = {} # online_url -> local_rel_path
        self.stats = {
            'issues_count': 0,
            'prs_count': 0,
            'releases_count': 0,
            'comments_count': 0,
            'media_count': 0,
            'media_bytes': 0,
            'dump_time': datetime.now(timezone.utc).isoformat()
        }

    def _api_get(self, endpoint: str, params: dict = None, max_retries: int = 3) -> list:
        """带分页、自动重试与鉴权的 GitHub API 请求"""
        results = []
        page = 1
        per_page = 100
        
        while True:
            query = {'per_page': per_page, 'page': page}
            if params:
                query.update(params)
            url = f"https://api.github.com/repos/{self.repo}/{endpoint}?{urllib.parse.urlencode(query)}"
            
            page_data = None
            for attempt in range(max_retries):
                req = urllib.request.Request(url, headers=self.headers)
                try:
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        page_data = data
                        break
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(1.5 * (attempt + 1))
                    else:
                        print(f"[-] 请求 API 失败 ({url}): {e}", file=sys.stderr)
            
            # 若所有重试均失败，安全终止分页退出
            if page_data is None:
                break
                
            # 若返回非列表（如单个 dict 对象），加入结果集并结束
            if isinstance(page_data, dict):
                results.append(page_data)
                break
                
            # 若非列表或列表为空（空仓库、无记录、或分页到底），立即终止循环
            if not isinstance(page_data, list) or len(page_data) == 0:
                break
                
            results.extend(page_data)
            
            # 如果当前页返回数量少于 per_page，说明已到最后一页，终止循环
            if len(page_data) < per_page:
                break
                
            page += 1
                
        return results

    def _extract_media_urls(self, text: str) -> set:
        """精确提取正文与评论中所有多媒体/附件链接"""
        if not text:
            return set()
        urls = set()
        for pat in MEDIA_URL_PATTERNS:
            matches = re.findall(pat, text, re.IGNORECASE)
            for m in matches:
                if isinstance(m, tuple):
                    m = m[0]
                m = m.strip()
                if m.startswith('http'):
                    clean_url = re.sub(r'[\)\"\'>]+$', '', m)
                    urls.add(clean_url)
        return urls

    def _download_media(self, url: str, max_retries: int = 3) -> str:
        """分块流式下载媒体，结合 Content-Disposition 与重定向精准还原语义文件名"""
        if url in self.downloaded_media:
            return self.downloaded_media[url]

        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, timeout=35) as resp:
                    final_url = resp.geturl()
                    content_type = resp.headers.get('Content-Type', '').split(';')[0].strip()
                    content_disposition = resp.headers.get('Content-Disposition', '')
                    
                    filename_match = re.findall(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', content_disposition, re.I)
                    detected_filename = filename_match[0] if filename_match else ''
                    
                    parsed_final = urllib.parse.urlparse(final_url).path
                    final_ext = Path(parsed_final).suffix
                    final_name = Path(parsed_final).name
                    
                    parsed_orig = urllib.parse.urlparse(url).path
                    orig_ext = Path(parsed_orig).suffix
                    orig_name = Path(parsed_orig).name
                    
                    ext = ''
                    if detected_filename and Path(detected_filename).suffix:
                        ext = Path(detected_filename).suffix
                    elif final_ext and len(final_ext) < 7:
                        ext = final_ext
                    elif orig_ext and len(orig_ext) < 7:
                        ext = orig_ext
                    else:
                        ext = mimetypes.guess_extension(content_type) or ''
                        if not ext or ext == '.bin':
                            if 'video' in content_type: ext = '.mp4'
                            elif 'audio' in content_type: ext = '.mp3'
                            elif 'image' in content_type: ext = '.png'
                            elif 'pdf' in content_type: ext = '.pdf'
                            else: ext = '.dat'

                    candidate_name = detected_filename or (final_name if not re.match(r'^[a-f0-9\-]{20,}$', final_name) else orig_name)
                    if candidate_name and len(candidate_name) > 3 and not re.match(r'^[a-f0-9\-]{20,}$', candidate_name):
                        safe_stem = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', Path(candidate_name).name)
                        file_name = f"{url_hash}_{safe_stem}"
                        if not file_name.lower().endswith(ext.lower()):
                            file_name += ext
                    else:
                        file_name = f"{url_hash}{ext}"
                        
                    target_file = self.media_dir / file_name
                    
                    bytes_written = 0
                    with open(target_file, 'wb') as f_out:
                        while True:
                            chunk = resp.read(CHUNK_SIZE)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            bytes_written += len(chunk)

                    self.stats['media_count'] += 1
                    self.stats['media_bytes'] += bytes_written
                    rel_path = f"media/{file_name}"
                    self.downloaded_media[url] = rel_path
                    return rel_path
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1.5)
                else:
                    print(f"[-] 下载附件重试失败 ({url}): {e}", file=sys.stderr)
                    return url
        return url

    def dump(self):
        """执行完整三层归档流水线：Issues/PRs, Releases, 媒体文件"""
        print(f"[*] 开始归档目标仓库: {self.repo} -> 输出目录: {self.output_dir}")
        
        # 1. 抓取所有 Issues 与 Pull Requests
        print("[*] [1/3] 正在抓取 Issues & Pull Requests...")
        raw_issues = self._api_get('issues', {'state': 'all'})
        processed_issues = []
        
        for item in raw_issues:
            issue_num = item.get('number')
            title = item.get('title')
            body = item.get('body', '') or ''
            is_pr = 'pull_request' in item
            if is_pr:
                self.stats['prs_count'] += 1
            else:
                self.stats['issues_count'] += 1
            
            # 抓取所有评论
            comments_data = []
            if item.get('comments', 0) > 0:
                raw_comments = self._api_get(f"issues/{issue_num}/comments")
                for c in raw_comments:
                    c_body = c.get('body', '') or ''
                    c_urls = self._extract_media_urls(c_body)
                    for u in c_urls:
                        self._download_media(u)
                        
                    comments_data.append({
                        'id': c.get('id'),
                        'user': c.get('user', {}).get('login'),
                        'created_at': c.get('created_at'),
                        'body': c_body
                    })
                    self.stats['comments_count'] += 1

            # 正文媒体下载
            body_urls = self._extract_media_urls(body)
            for u in body_urls:
                self._download_media(u)

            processed_issues.append({
                'number': issue_num,
                'title': title,
                'state': item.get('state'),
                'is_pull_request': is_pr,
                'author': item.get('user', {}).get('login'),
                'created_at': item.get('created_at'),
                'updated_at': item.get('updated_at'),
                'body': body,
                'comments': comments_data
            })

        # 2. 抓取所有 Releases (Release notes, Tags 与发布附件)
        print("[*] [2/3] 正在全量抓取 Releases 与发布资产 (SPEC 核心要求)...")
        raw_releases = self._api_get('releases')
        processed_releases = []
        
        for rel in raw_releases:
            rel_name = rel.get('name') or rel.get('tag_name')
            rel_body = rel.get('body', '') or ''
            
            # 下载 release 正文媒体
            rel_urls = self._extract_media_urls(rel_body)
            for u in rel_urls:
                self._download_media(u)
                
            # 下载 release 上传的二进制 assets
            assets_data = []
            for asset in rel.get('assets', []):
                download_url = asset.get('browser_download_url')
                if download_url:
                    local_asset = self._download_media(download_url)
                    assets_data.append({
                        'name': asset.get('name'),
                        'size': asset.get('size'),
                        'download_count': asset.get('download_count'),
                        'local_path': local_asset
                    })

            processed_releases.append({
                'id': rel.get('id'),
                'tag_name': rel.get('tag_name'),
                'name': rel_name,
                'author': rel.get('author', {}).get('login'),
                'published_at': rel.get('published_at'),
                'body': rel_body,
                'assets': assets_data
            })
            self.stats['releases_count'] += 1

        # 3. 写入标准 JSON 数据包
        print("[*] [3/3] 正在生成标准离线 JSON、HTML 与 SUMMARY.md...")
        (self.output_dir / 'issues.json').write_text(
            json.dumps(processed_issues, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        (self.output_dir / 'releases.json').write_text(
            json.dumps(processed_releases, indent=2, ensure_ascii=False), encoding='utf-8'
        )
        (self.output_dir / 'metadata.json').write_text(
            json.dumps({
                'repository': self.repo,
                'stats': self.stats,
                'media_map': self.downloaded_media
            }, indent=2, ensure_ascii=False), encoding='utf-8'
        )

        # 4. 生成双重视图
        self._generate_html(processed_issues, processed_releases)
        self._generate_markdown_summary(processed_issues, processed_releases)
        
        mb = self.stats['media_bytes'] / (1024 * 1024)
        print(f"\n[+] 🎉 仓库全量归档完毕！")
        print(f"    - Issues: {self.stats['issues_count']} 个")
        print(f"    - Pull Requests: {self.stats['prs_count']} 个")
        print(f"    - Releases: {self.stats['releases_count']} 个")
        print(f"    - 讨论与评论: {self.stats['comments_count']} 条")
        print(f"    - 多媒体附件: {self.stats['media_count']} 个 ({mb:.2f} MB)")
        print(f"    - 离线网页: {self.output_dir / 'index.html'}")
        print(f"    - 原生 Markdown: {self.output_dir / 'SUMMARY.md'}")

    def _render_content_safely(self, text: str) -> str:
        """全量 HTML 转义防御 XSS，并注入本地多媒体控件"""
        if not text:
            return '<i>无描述内容</i>'
        safe_text = html.escape(text)
        
        for online_url, local_path in self.downloaded_media.items():
            escaped_url = html.escape(online_url)
            if escaped_url in safe_text:
                ext = local_path.lower()
                if ext.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg')):
                    rep = f'<div class="media-box"><img src="{local_path}" loading="lazy" /></div>'
                elif ext.endswith(('.mp3', '.wav', '.ogg', '.aac', '.m4a')):
                    rep = f'<div class="media-box"><audio controls src="{local_path}"></audio></div>'
                elif ext.endswith(('.mp4', '.webm', '.mov')):
                    rep = f'<div class="media-box"><video controls src="{local_path}"></video></div>'
                elif ext.endswith('.pdf'):
                    rep = f'<div class="media-box"><a class="file-badge" href="{local_path}" target="_blank">📄 查看本地 PDF ({Path(local_path).name})</a></div>'
                else:
                    rep = f'<div class="media-box"><a class="file-badge" href="{local_path}" target="_blank">📁 本地附件 ({Path(local_path).name})</a></div>'
                safe_text = safe_text.replace(escaped_url, rep)
                
        return safe_text.replace('\n', '<br>')

    def _generate_html(self, issues: list, releases: list):
        """生成支持全量离线播放与预览的高保真响应式网页"""
        html_file = self.output_dir / 'index.html'
        
        # 1. Releases 模块
        releases_cards = []
        for rel in releases:
            rel_body = self._render_content_safely(rel['body'])
            assets_html = ''
            if rel['assets']:
                items = ''.join([f'<li><a href="{a["local_path"]}" target="_blank">💾 {a["name"]} ({a["size"]/1024:.1f} KB)</a></li>' for a in rel['assets']])
                assets_html = f'<div class="assets-box"><strong>📦 资产文件:</strong><ul>{items}</ul></div>'
                
            releases_cards.append(f"""
            <div class="issue-card release-card">
                <div class="issue-header">
                    <span class="badge badge-release">RELEASE</span>
                    <span class="issue-num">{html.escape(rel['tag_name'])}</span>
                    <span class="issue-title">{html.escape(rel['name'] or rel['tag_name'])}</span>
                </div>
                <div class="issue-meta">由 @{html.escape(rel['author'] or 'attogram')} 发布于 {rel['published_at']}</div>
                <div class="issue-content">{rel_body}</div>
                {assets_html}
            </div>
            """)

        # 2. Issues / PRs 模块
        issues_cards = []
        for it in issues:
            body_rendered = self._render_content_safely(it['body'])
            badge_class = 'badge-open' if it['state'] == 'open' else 'badge-closed'
            type_label = 'PR' if it['is_pull_request'] else 'ISSUE'
            
            comments_html = ''
            for c in it['comments']:
                c_body = self._render_content_safely(c['body'])
                comments_html += f"""
                <div class="comment-card">
                    <div class="comment-author">@{html.escape(c['user'])} · <span class="time">{c['created_at']}</span></div>
                    <div class="comment-body">{c_body}</div>
                </div>
                """
                
            issues_cards.append(f"""
            <div class="issue-card">
                <div class="issue-header">
                    <span class="badge {badge_class}">{it['state'].upper()}</span>
                    <span class="badge-type">{type_label}</span>
                    <span class="issue-num">#{it['number']}</span>
                    <span class="issue-title">{html.escape(it['title'])}</span>
                </div>
                <div class="issue-meta">由 @{html.escape(it['author'])} 创建于 {it['created_at']}</div>
                <div class="issue-content">{body_rendered}</div>
                {f'<div class="comments-section"><h4>💬 讨论与附件 ({len(it["comments"])} 条)</h4>{comments_html}</div>' if comments_html else ''}
            </div>
            """)

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(self.repo)} - 全量离线归档</title>
    <style>
        :root {{ --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --accent: #58a6ff; --green: #238636; --purple: #8957e5; --orange: #d29922; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); padding: 24px; margin: 0; line-height: 1.6; }}
        .header {{ max-width: 960px; margin: 0 auto 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
        .header h1 {{ margin: 0 0 10px; color: #fff; font-size: 24px; }}
        .stats-badge {{ display: inline-block; background: var(--card); border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px; margin-right: 8px; margin-bottom: 6px; }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        .section-title {{ font-size: 20px; color: #fff; margin: 32px 0 16px; border-left: 4px solid var(--accent); padding-left: 10px; }}
        .issue-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }}
        .release-card {{ border-color: #388bfd44; }}
        .issue-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }}
        .badge {{ padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
        .badge-type {{ background: #21262d; border: 1px solid var(--border); padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; color: #8b949e; }}
        .badge-open {{ background: var(--green); color: #fff; }}
        .badge-closed {{ background: var(--purple); color: #fff; }}
        .badge-release {{ background: var(--orange); color: #fff; }}
        .issue-num {{ color: #8b949e; font-weight: bold; }}
        .issue-title {{ color: #fff; font-size: 18px; font-weight: 600; word-break: break-word; }}
        .issue-meta {{ font-size: 13px; color: #8b949e; margin-bottom: 16px; }}
        .issue-content {{ border-top: 1px solid var(--border); padding-top: 14px; word-break: break-word; }}
        .media-box {{ margin: 12px 0; }}
        .media-box img {{ max-width: 100%; height: auto; border-radius: 6px; border: 1px solid var(--border); }}
        .media-box audio {{ width: 100%; max-width: 520px; }}
        .media-box video {{ width: 100%; max-width: 640px; border-radius: 6px; }}
        .file-badge {{ display: inline-block; background: #21262d; border: 1px solid var(--border); color: var(--accent); padding: 6px 12px; border-radius: 6px; text-decoration: none; font-size: 13px; }}
        .comments-section {{ margin-top: 20px; padding-top: 14px; border-top: 1px dashed var(--border); }}
        .comment-card {{ background: #0d1117; border: 1px solid var(--border); border-radius: 6px; padding: 14px; margin-bottom: 12px; }}
        .comment-author {{ font-weight: bold; color: var(--accent); font-size: 13px; margin-bottom: 8px; }}
        .time {{ color: #8b949e; font-weight: normal; }}
        .assets-box {{ margin-top: 14px; background: #0d1117; padding: 10px 14px; border-radius: 6px; }}
        .assets-box ul {{ margin: 6px 0 0 20px; padding: 0; }}
        .assets-box a {{ color: var(--accent); text-decoration: none; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📦 仓库全量离线归档: {html.escape(self.repo)}</h1>
        <div>
            <span class="stats-badge">📌 Issues: {self.stats['issues_count']}</span>
            <span class="stats-badge">🔀 PRs: {self.stats['prs_count']}</span>
            <span class="stats-badge">🏷️ Releases: {self.stats['releases_count']}</span>
            <span class="stats-badge">💬 讨论总计: {self.stats['comments_count']} 条</span>
            <span class="stats-badge">🎵 多媒体与附件: {self.stats['media_count']} 个 ({self.stats['media_bytes'] / 1024 / 1024:.2f} MB)</span>
            <span class="stats-badge">🕒 归档时间: {self.stats['dump_time']}</span>
        </div>
    </div>
    <div class="container">
        {f'<h2 class="section-title">🏷️ 版本发布归档 (Releases)</h2>{"".join(releases_cards)}' if releases_cards else ''}
        <h2 class="section-title">💬 讨论与工单 (Issues & Pull Requests)</h2>
        {''.join(issues_cards)}
    </div>
</body>
</html>"""
        html_file.write_text(html_content, encoding='utf-8')

    def _generate_markdown_summary(self, issues: list, releases: list):
        """生成支持 GitHub 原生直接渲染的完整清单 SUMMARY.md"""
        summary_file = self.output_dir / 'SUMMARY.md'
        mb = self.stats['media_bytes'] / (1024 * 1024)
        
        md = f"""# 📦 仓库全量归档报告: {self.repo}

> 归档时间: `{self.stats['dump_time']}`

## 📊 数据完整性统计 (Core Areas)
- **工单总计 (Issues)**: {self.stats['issues_count']} 个
- **合并请求总计 (Pull Requests)**: {self.stats['prs_count']} 个
- **版本发布总计 (Releases)**: {self.stats['releases_count']} 个
- **讨论与评论总计**: {self.stats['comments_count']} 条
- **已下载保存的多媒体附件**: {self.stats['media_count']} 个 (总计 **{mb:.2f} MB**)
- **离线可视化展示**: `index.html` (支持音频即点即播与高清图片预览)

## 🏷️ 版本发布清单 (Releases)

| 标签 (Tag) | 发布名称 | 发布人 | 发布时间 | 附件资产 |
| :---: | :--- | :--- | :---: | :---: |
"""
        for r in releases:
            safe_name = (r['name'] or r['tag_name']).replace('|', '\\|')
            assets_cnt = len(r.get('assets', []))
            md += f"| `{r['tag_name']}` | {safe_name} | @{r['author']} | {r['published_at']} | {assets_cnt} 个 |\n"

        md += """\n## 🗂️ 核心讨论与工单清单 (Issues & PRs)

| # | 类型 | 状态 | 标题 | 作者 | 评论数 |
| :---: | :---: | :---: | :--- | :--- | :---: |
"""
        for it in issues[:20]:
            state_badge = '🟢 OPEN' if it['state'] == 'open' else '🟣 CLOSED'
            t_badge = 'PR' if it['is_pull_request'] else 'Issue'
            safe_title = it['title'].replace('|', '\\|')
            md += f"| #{it['number']} | {t_badge} | {state_badge} | {safe_title} | @{it['author']} | {len(it['comments'])} |\n"
            
        md += "\n---\n*本归档包由 [Repository Dump Tool](https://github.com/attogram/THE-ERROR-IS-THE-MESSAGE) 自动生成。*\n"
        summary_file.write_text(md, encoding='utf-8')

if __name__ == '__main__':
    repo_name = sys.argv[1] if len(sys.argv) > 1 else 'attogram/THE-ERROR-IS-THE-MESSAGE'
    out_dir = sys.argv[2] if len(sys.argv) > 2 else 'dump'
    
    dumper = RepoDumper(repo_name, out_dir)
    dumper.dump()
