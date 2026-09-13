#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库归档工具单元测试套件 (Test Suite for Repo Dump Tool)
基于 Python 3 标准库 unittest，无任何第三方依赖。
覆盖：媒体正则提取、文件名探测解析、XSS防御转义、双重视图生成。
"""

import unittest
import tempfile
import shutil
import json
from pathlib import Path
from tools.repo_dump import RepoDumper

class TestRepoDumper(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.dumper = RepoDumper(repo='attogram/THE-ERROR-IS-THE-MESSAGE', output_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_extract_media_urls_complex(self):
        """测试对复杂多层附件、图片、音频、PDF 的正则提取"""
        sample_text = """
        Here is a test PDF: [bounty.repo.dump.0001.pdf](https://github.com/user-attachments/files/32157681/bounty.repo.dump.0001.pdf)
        Here is an audio asset: https://github.com/user-attachments/assets/85df85e8-6546-49ad-81d7-93debdfc6c92
        Here is an HTML image: <img width="1536" height="2048" alt="Image" src="https://github.com/user-attachments/assets/cb7b2e93-7ebd-41ea-b292-0d27d60a122e" />
        Here is a markdown image: ![Alt Text](https://github.com/user-attachments/assets/210cfc13-2729-4f54-8d08-6d7fc8e6a114)
        """
        urls = self.dumper._extract_media_urls(sample_text)
        
        self.assertIn('https://github.com/user-attachments/files/32157681/bounty.repo.dump.0001.pdf', urls)
        self.assertIn('https://github.com/user-attachments/assets/85df85e8-6546-49ad-81d7-93debdfc6c92', urls)
        self.assertIn('https://github.com/user-attachments/assets/cb7b2e93-7ebd-41ea-b292-0d27d60a122e', urls)
        self.assertIn('https://github.com/user-attachments/assets/210cfc13-2729-4f54-8d08-6d7fc8e6a114', urls)

    def test_html_safe_rendering_and_xss(self):
        """测试 HTML 渲染中的 XSS 防御与克林贡语特殊字符"""
        malicious_or_special_text = """
        <script>alert('xss')</script>
        Klingon sample: Duj legh suvwI' & <test>
        Media reference: https://github.com/user-attachments/assets/test-audio
        """
        # 模拟已下载的媒体映射
        self.dumper.downloaded_media['https://github.com/user-attachments/assets/test-audio'] = 'media/test_audio.mp3'
        rendered = self.dumper._render_content_safely(malicious_or_special_text)
        
        # 验证恶意脚本已被安全转义
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('&lt;test&gt;', rendered)
        # 验证媒体控件被正确注入
        self.assertIn('<audio controls src="media/test_audio.mp3"></audio>', rendered)

    def test_view_generation(self):
        """测试 HTML 与 SUMMARY.md 双重视图的生成正确性"""
        sample_issues = [{
            'number': 60,
            'title': 'BOUNTY: REPOSITORY DUMP TOOL',
            'state': 'open',
            'is_pull_request': False,
            'author': 'attogram',
            'created_at': '2026-09-13T10:20:00Z',
            'body': 'Dump all repository data',
            'comments': []
        }]
        sample_releases = [{
            'id': 1,
            'tag_name': '0003',
            'name': 'Release 0003',
            'author': 'attogram',
            'published_at': '2026-09-13T09:00:00Z',
            'body': 'Initial release notes',
            'assets': []
        }]
        
        self.dumper._generate_html(sample_issues, sample_releases)
        self.dumper._generate_markdown_summary(sample_issues, sample_releases)
        
        html_file = Path(self.test_dir) / 'index.html'
        summary_file = Path(self.test_dir) / 'SUMMARY.md'
        
        self.assertTrue(html_file.exists())
        self.assertTrue(summary_file.exists())
        
        html_content = html_file.read_text(encoding='utf-8')
        summary_content = summary_file.read_text(encoding='utf-8')
        
        self.assertIn('BOUNTY: REPOSITORY DUMP TOOL', html_content)
        self.assertIn('Release 0003', html_content)
        self.assertIn('#60', summary_content)
        self.assertIn('`0003`', summary_content)

if __name__ == '__main__':
    unittest.main()
