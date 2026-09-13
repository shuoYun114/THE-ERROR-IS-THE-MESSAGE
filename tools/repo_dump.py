#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库全量数据与多媒体附件归档工具 (Repository Dump Tool - Enterprise Grade)
完全基于 Python 3 标准库，零第三方外部依赖。
核心特性：
1. 3重文件名与MIME探测（Content-Disposition + 重定向URL + 响应头检测）
2. 内存友好分块流式下载（Chunked Streaming，零OOM风险）+ 3次网络自动重试
3. 严格HTML安全转义（XSS防御，完美支持克林贡语与特殊排版）
4. 双重可视化输出：支持多媒体本地直放的 index.html 与 GitHub 原生渲染的 SUMMARY.md
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

# 附件与多媒体链接匹配正则 (覆盖全路径 assets、files、HTML 及 Markdown 链接)
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
            'User-Agent': 'Repo-Dump-Tool/2.0',
            'Accept': 'application/vnd.github.v3+json'
        }
        if self.token:
            self.headers['Authorization'] = f'Bearer {self.token}'
            
        self.downloaded_media = {} # online_url -> local_rel_path
        self.stats = {
            'issues_count': 0,
            'comments_count': 0,
            'media_count': 0,
            'media_bytes': 0,
            'dump_time': datetime.now(timezone.utc).isoformat()
        }

    def _api_get(self, endpoint: str, params: dict = None, max_retries: int = 3) -> list:
        """带分页、重试与鉴权的 GitHub API 请求"""
        results = []
        page = 1
        per_page = 100
        
        while True:
            query = {'per_page': per_page, 'page': page}
            if params:
                query.update(params)
            url = f"https://api.github.com/repos/{self.repo}/{endpoint}?{urllib.parse.urlencode(query)}"
            
            success = False
            for attempt in range(max_retries):
                req = urllib.request.Request(url, headers=self.headers)
                try:
                    with urllib.request.urlopen(req, timeout=25) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        if not data or not isinstance(data, list):
                            if isinstance(data, dict):
                                results.append(data)
                            success = True
                            break
                        results.extend(data)
                        if len(data) < per_page:
                            success = True
                            break
                        page += 1
                        success = True
                        break
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(1.5 * (attempt + 1))
                    else:
                        print(f"[-] 请求 API 失败 ({url}): {e}", file=sys.stderr)
            
            if not success or (len(results) > 0 and len(results) % per_page != 0):
                break
                
        return results

    def _extract_media_urls(self, text: str) -> set:
        """从正文中精确提取所有多媒体与附件链接"""
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
                    # 清洗尾部多余标点
                    clean_url = re.sub(r'[\)\"\'>]+$', '', m)
                    urls.add(clean_url)
        return urls

    def _download_media(self, url: str, max_retries: int = 3) -> str:
        """分块流式下载多媒体文件，结合 Content-Disposition 与重定向精准保留文件名"""
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
                    
                    # 1. 尝试从 Content-Disposition 提取真实文件名
                    filename_match = re.findall(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', content_disposition, re.I)
                    detected_filename = filename_match[0] if filename_match else ''
                    
                    # 2. 尝试从重定向后的 final_url 提取
                    parsed_final = urllib.parse.urlparse(final_url).path
                    final_ext = Path(parsed_final).suffix
                    final_name = Path(parsed_final).name
                    
                    # 3. 尝试从原始 url 提取
                    parsed_orig = urllib.parse.urlparse(url).path
                    orig_ext = Path(parsed_orig).suffix
                    orig_name = Path(parsed_orig).name
                    
                    # 扩展名推断优先级：Content-Disposition -> final_ext -> orig_ext -> MIME
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

                    # 文件名组装（保留语义名称）
                    candidate_name = detected_filename or (final_name if not re.match(r'^[a-f0-9\-]{20,}$', final_name) else orig_name)
                    if candidate_name and len(candidate_name) > 3 and not re.match(r'^[a-f0-9\-]{20,}$', candidate_name):
                        safe_stem = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', Path(candidate_name).name)
                        file_name = f"{url_hash}_{safe_stem}"
                        if not file_name.lower().endswith(ext.lower()):
                            file_name += ext
                    else:
                        file_name = f"{url_hash}{ext}"
                        
                    target_file = self.media_dir / file_name
                    
                    # 分块流式写入，杜绝大文件内存暴涨
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
        """执行完整归档流水线"""
        print(f"[*] 开始归档目标仓库: {self.repo} -> 输出目录: {self.output_dir}")
        
        # 1. 抓取所有 Issues 与 Pull Requests
        print("[*] 正在全量获取 Issues & PRs...")
        raw_issues = self._api_get('issues', {'state': 'all'})
        processed_issues = []
        
        for item in raw_issues:
            issue_num = item.get('number')
            title = item.get('title')
            body = item.get('body', '') or ''
            is_pr = 'pull_request' in item
            
            # 抓取评论
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
            self.stats['issues_count'] += 1

        # 2. 写入 JSON 数据契约与元数据
        data_file = self.output_dir / 'issues.json'
        data_file.write_text(json.dumps(processed_issues, indent=2, ensure_ascii=False), encoding='utf-8')
        
        metadata_file = self.output_dir / 'metadata.json'
        metadata_file.write_text(json.dumps({
            'repository': self.repo,
            'stats': self.stats,
            'media_map': self.downloaded_media
        }, indent=2, ensure_ascii=False), encoding='utf-8')

        # 3. 生成双重视图：离线交互 HTML 与 GitHub 原生 SUMMARY.md
        self._generate_html(processed_issues)
        self._generate_markdown_summary(processed_issues)
        
        mb = self.stats['media_bytes'] / (1024 * 1024)
        print(f"\n[+] 仓库全量归档完毕！")
        print(f"    - Issues / PRs: {self.stats['issues_count']} 项")
        print(f"    - 讨论与评论: {self.stats['comments_count']} 条")
        print(f"    - 多媒体与附件: {self.stats['media_count']} 个 ({mb:.2f} MB)")
        print(f"    - 离线交互网页: {self.output_dir / 'index.html'}")
        print(f"    - 原生 Markdown: {self.output_dir / 'SUMMARY.md'}")

    def _render_content_safely(self, text: str) -> str:
        """严格做 HTML 转义以防御 XSS，然后注入本地媒体播放控件"""
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

    def _generate_html(self, issues: list):
        """生成带安全转义、暗黑模式、原生音视频播放器的响应式单页面"""
        html_file = self.output_dir / 'index.html'
        
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
    <title>{html.escape(self.repo)} - 离线全量数据与多媒体归档库</title>
    <style>
        :root {{ --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --accent: #58a6ff; --green: #238636; --purple: #8957e5; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); padding: 24px; margin: 0; line-height: 1.6; }}
        .header {{ max-width: 960px; margin: 0 auto 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
        .header h1 {{ margin: 0 0 10px; color: #fff; font-size: 24px; }}
        .stats-badge {{ display: inline-block; background: var(--card); border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px; margin-right: 8px; margin-bottom: 6px; }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        .issue-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }}
        .issue-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; flex-wrap: wrap; }}
        .badge {{ padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
        .badge-type {{ background: #21262d; border: 1px solid var(--border); padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; color: #8b949e; }}
        .badge-open {{ background: var(--green); color: #fff; }}
        .badge-closed {{ background: var(--purple); color: #fff; }}
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
    </style>
</head>
<body>
    <div class="header">
        <h1>📦 仓库全量离线归档: {html.escape(self.repo)}</h1>
        <div>
            <span class="stats-badge">📌 Issues & PRs: {self.stats['issues_count']}</span>
            <span class="stats-badge">💬 讨论总计: {self.stats['comments_count']} 条</span>
            <span class="stats-badge">🎵 多媒体与附件: {self.stats['media_count']} 个 ({self.stats['media_bytes'] / 1024 / 1024:.2f} MB)</span>
            <span class="stats-badge">🕒 归档时间: {self.stats['dump_time']}</span>
        </div>
    </div>
    <div class="container">
        {''.join(issues_cards)}
    </div>
</body>
</html>"""
        html_file.write_text(html_content, encoding='utf-8')

    def _generate_markdown_summary(self, issues: list):
        """生成支持 GitHub 原生直接渲染阅读的 SUMMARY.md"""
        summary_file = self.output_dir / 'SUMMARY.md'
        mb = self.stats['media_bytes'] / (1024 * 1024)
        
        md = f"""# 📦 仓库全量归档概览: {self.repo}

> 归档时间: `{self.stats['dump_time']}`

## 📊 数据统计
- **Issues & Pull Requests 总计**: {self.stats['issues_count']} 项
- **讨论与评论总计**: {self.stats['comments_count']} 条
- **已下载保存的多媒体附件**: {self.stats['media_count']} 个 (总计 **{mb:.2f} MB**)
- **离线浏览页面**: `index.html` (支持音频即点即播与高清图片预览)

## 🗂️ 核心 Issue 与附件清单 (前 15 项)

| # | 类型 | 状态 | 标题 | 作者 | 评论数 |
| :---: | :---: | :---: | :--- | :--- | :---: |
"""
        for it in issues[:15]:
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
