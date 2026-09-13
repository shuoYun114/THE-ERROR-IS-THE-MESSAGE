#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库全量数据与多媒体附件归档工具 (Repository Dump Tool)
完全基于 Python 3 标准库，零第三方依赖。
支持全量抓取 Issues、Pull Requests、Comments、Commit 摘要及所有嵌入多媒体附件（图片、音频、PDF、视频）。
"""

import os
import sys
import json
import re
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
    # GitHub user-attachments (图片、音频、视频、PDF)
    r'https?://github\.com/user-attachments/(?:assets|files)/[a-zA-Z0-9_\-]+(?:\.[a-zA-Z0-9]+)?',
    # 纯 HTML 标签中的 src
    r'<img\s+[^>]*?src=["\'](https?://[^"\']+)["\']',
    r'<audio\s+[^>]*?src=["\'](https?://[^"\']+)["\']',
    r'<video\s+[^>]*?src=["\'](https?://[^"\']+)["\']',
    # Markdown 媒体语法
    r'!\[.*?\]\((https?://[^\s\)]+)\)',
]

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
            'User-Agent': 'Repo-Dump-Tool/1.0',
            'Accept': 'application/vnd.github.v3+json'
        }
        if self.token:
            self.headers['Authorization'] = f'Bearer {self.token}'
            
        self.downloaded_media = {} # url -> local_rel_path
        self.stats = {
            'issues_count': 0,
            'comments_count': 0,
            'media_count': 0,
            'media_bytes': 0,
            'dump_time': datetime.now(timezone.utc).isoformat()
        }

    def _api_get(self, endpoint: str, params: dict = None) -> list:
        """带分页与鉴权的 GitHub API 请求"""
        results = []
        page = 1
        per_page = 100
        
        while True:
            query = {'per_page': per_page, 'page': page}
            if params:
                query.update(params)
            url = f"https://api.github.com/repos/{self.repo}/{endpoint}?{urllib.parse.urlencode(query)}"
            
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if not data or not isinstance(data, list):
                        if isinstance(data, dict):
                            results.append(data)
                        break
                    results.extend(data)
                    if len(data) < per_page:
                        break
                    page += 1
            except Exception as e:
                print(f"[-] 请求 API 发生异常 ({url}): {e}", file=sys.stderr)
                break
                
        return results

    def _extract_media_urls(self, text: str) -> set:
        """从 Markdown 与 HTML 文本中精确提取所有多媒体与附件链接"""
        if not text:
            return set()
        urls = set()
        for pat in MEDIA_URL_PATTERNS:
            matches = re.findall(pat, text, re.IGNORECASE)
            for m in matches:
                if isinstance(m, tuple):
                    m = m[0]
                if m.startswith('http'):
                    urls.add(m)
        return urls

    def _download_media(self, url: str) -> str:
        """下载多媒体文件并保留 SHA256 校验和与扩展名"""
        if url in self.downloaded_media:
            return self.downloaded_media[url]

        url_hash = hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
                content_type = resp.headers.get('Content-Type', '').split(';')[0].strip()
                ext = mimetypes.guess_extension(content_type) or ''
                
                # 若 mime 无法推断，尝试从 url 提取
                if not ext or ext == '.bin':
                    parsed_path = urllib.parse.urlparse(url).path
                    base_ext = Path(parsed_path).suffix
                    if base_ext and len(base_ext) < 6:
                        ext = base_ext
                    elif 'image' in content_type:
                        ext = '.png'
                    elif 'audio' in content_type:
                        ext = '.mp3'
                    elif 'pdf' in content_type:
                        ext = '.pdf'
                    elif 'video' in content_type:
                        ext = '.mp4'
                    else:
                        ext = '.dat'
                        
                file_name = f"{url_hash}{ext}"
                target_file = self.media_dir / file_name
                target_file.write_bytes(content)
                
                self.stats['media_count'] += 1
                self.stats['media_bytes'] += len(content)
                rel_path = f"media/{file_name}"
                self.downloaded_media[url] = rel_path
                return rel_path
        except Exception as e:
            print(f"[-] 下载附件失败 ({url}): {e}", file=sys.stderr)
            return url

    def dump(self):
        """执行完整归档流水线"""
        print(f"[*] 开始归档仓库: {self.repo} -> 输出目录: {self.output_dir}")
        
        # 1. 抓取 Issues 与 PRs
        print("[*] 正在抓取 Issues / PRs...")
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
                    # 提取并下载评论内的媒体
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

            # 提取并下载 Issue 正文内的媒体
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

        # 2. 导出 JSON 数据包
        data_file = self.output_dir / 'issues.json'
        data_file.write_text(json.dumps(processed_issues, indent=2, ensure_ascii=False), encoding='utf-8')
        
        metadata_file = self.output_dir / 'metadata.json'
        metadata_file.write_text(json.dumps({
            'repository': self.repo,
            'stats': self.stats,
            'media_map': self.downloaded_media
        }, indent=2, ensure_ascii=False), encoding='utf-8')

        # 3. 生成精美的离线 HTML 视图
        self._generate_html(processed_issues)
        
        print(f"\n[+] 归档完成！")
        print(f"    - Issues / PRs: {self.stats['issues_count']} 个")
        print(f"    - 评论总数: {self.stats['comments_count']} 条")
        print(f"    - 媒体附件: {self.stats['media_count']} 个 ({self.stats['media_bytes'] / 1024 / 1024:.2f} MB)")
        print(f"    - 离线网页: {self.output_dir / 'index.html'}")

    def _generate_html(self, issues: list):
        """生成支持离线浏览音频、图片、文本的单页面 HTML"""
        html_file = self.output_dir / 'index.html'
        
        issues_cards = []
        for it in issues:
            # 替换正文中的媒体链接为本地下载的路径
            body_html = it['body'] or '<i>无描述内容</i>'
            for online_url, local_path in self.downloaded_media.items():
                if online_url in body_html:
                    if local_path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                        rep = f'<div class="media-box"><img src="{local_path}" loading="lazy" /></div>'
                    elif local_path.endswith(('.mp3', '.wav', '.ogg')):
                        rep = f'<div class="media-box"><audio controls src="{local_path}"></audio></div>'
                    elif local_path.endswith(('.mp4', '.webm')):
                        rep = f'<div class="media-box"><video controls src="{local_path}"></video></div>'
                    else:
                        rep = f'<div class="media-box"><a href="{local_path}" target="_blank">📁 本地附件 ({Path(local_path).name})</a></div>'
                    body_html = body_html.replace(online_url, rep)
            
            # 转义简单换行
            body_rendered = body_html.replace('\n', '<br>')
            badge_class = 'badge-open' if it['state'] == 'open' else 'badge-closed'
            
            comments_html = ''
            for c in it['comments']:
                c_body = c['body'].replace('\n', '<br>')
                for online_url, local_path in self.downloaded_media.items():
                    if online_url in c_body:
                        if local_path.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                            rep = f'<div class="media-box"><img src="{local_path}" loading="lazy" /></div>'
                        elif local_path.endswith(('.mp3', '.wav', '.ogg')):
                            rep = f'<div class="media-box"><audio controls src="{local_path}"></audio></div>'
                        else:
                            rep = f'<div class="media-box"><a href="{local_path}" target="_blank">📁 本地附件</a></div>'
                        c_body = c_body.replace(online_url, rep)
                        
                comments_html += f"""
                <div class="comment-card">
                    <div class="comment-author">@{c['user']} · <span class="time">{c['created_at']}</span></div>
                    <div class="comment-body">{c_body}</div>
                </div>
                """
                
            issues_cards.append(f"""
            <div class="issue-card">
                <div class="issue-header">
                    <span class="badge {badge_class}">{it['state'].upper()}</span>
                    <span class="issue-num">#{it['number']}</span>
                    <span class="issue-title">{it['title']}</span>
                </div>
                <div class="issue-meta">由 @{it['author']} 创建于 {it['created_at']}</div>
                <div class="issue-content">{body_rendered}</div>
                {f'<div class="comments-section"><h4>💬 讨论与附件 ({len(it["comments"])} 条)</h4>{comments_html}</div>' if comments_html else ''}
            </div>
            """)

        html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.repo} - 离线数据与多媒体归档库</title>
    <style>
        :root {{ --bg: #0d1117; --card: #161b22; --border: #30363d; --text: #c9d1d9; --accent: #58a6ff; --green: #238636; --purple: #8957e5; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); padding: 24px; margin: 0; line-height: 1.6; }}
        .header {{ max-width: 960px; margin: 0 auto 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
        .header h1 {{ margin: 0 0 8px; color: #fff; font-size: 24px; }}
        .stats-badge {{ display: inline-block; background: var(--card); border: 1px solid var(--border); padding: 6px 12px; border-radius: 6px; font-size: 13px; margin-right: 8px; }}
        .container {{ max-width: 960px; margin: 0 auto; }}
        .issue-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
        .issue-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }}
        .badge {{ padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: bold; text-transform: uppercase; }}
        .badge-open {{ background: var(--green); color: #fff; }}
        .badge-closed {{ background: var(--purple); color: #fff; }}
        .issue-num {{ color: #8b949e; font-weight: bold; }}
        .issue-title {{ color: #fff; font-size: 18px; font-weight: 600; }}
        .issue-meta {{ font-size: 13px; color: #8b949e; margin-bottom: 16px; }}
        .issue-content {{ border-top: 1px solid var(--border); padding-top: 14px; word-break: break-word; }}
        .media-box {{ margin: 12px 0; }}
        .media-box img {{ max-width: 100%; height: auto; border-radius: 6px; border: 1px solid var(--border); }}
        .media-box audio {{ width: 100%; max-width: 480px; }}
        .comments-section {{ margin-top: 20px; padding-top: 14px; border-top: 1px dashed var(--border); }}
        .comment-card {{ background: #0d1117; border: 1px solid var(--border); border-radius: 6px; padding: 12px; margin-bottom: 10px; }}
        .comment-author {{ font-weight: bold; color: var(--accent); font-size: 13px; margin-bottom: 6px; }}
        .time {{ color: #8b949e; font-weight: normal; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📦 仓库全量离线归档: {self.repo}</h1>
        <div>
            <span class="stats-badge">📌 Issues/PRs: {self.stats['issues_count']}</span>
            <span class="stats-badge">💬 评论总计: {self.stats['comments_count']}</span>
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

if __name__ == '__main__':
    repo_name = sys.argv[1] if len(sys.argv) > 1 else 'attogram/THE-ERROR-IS-THE-MESSAGE'
    out_dir = sys.argv[2] if len(sys.argv) > 2 else 'dump'
    
    dumper = RepoDumper(repo_name, out_dir)
    dumper.dump()
