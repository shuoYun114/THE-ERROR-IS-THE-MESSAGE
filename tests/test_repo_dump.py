#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
仓库归档工具全量工业级单元测试套件 (Comprehensive Test Suite for Repo Dump Tool)
基于 Python 3 标准库 unittest，无任何第三方依赖。
包含 24 个高覆盖率测试用例，覆盖：
- API 分页边界与终止
- 正则提取与多媒体识别
- XSS 防御与安全 HTML/Markdown 渲染
- 文件名语义解析与防碰撞
- 分块流式数据一致性与缓存防重复下载
- 数据结构输出标准
"""

import unittest
import tempfile
import shutil
import json
import os
import hashlib
from unittest.mock import patch, MagicMock
from pathlib import Path
from tools.repo_dump import RepoDumper

class TestRepoDumper(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.dumper = RepoDumper(repo='attogram/THE-ERROR-IS-THE-MESSAGE', output_dir=self.test_dir)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # --- 1. 正则与附件捕获测试 ---
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

    def test_extract_empty_and_none(self):
        """测试对空字符串和 None 对象的鲁棒提取"""
        self.assertEqual(self.dumper._extract_media_urls(''), set())
        self.assertEqual(self.dumper._extract_media_urls(None), set())

    def test_extract_video_audio_tags(self):
        """测试直接从 <video> 和 <audio> 标签中提取媒体链接"""
        sample = '<video src="https://example.com/demo.mp4"></video><audio src="https://example.com/sound.mp3"></audio>'
        urls = self.dumper._extract_media_urls(sample)
        self.assertIn('https://example.com/demo.mp4', urls)
        self.assertIn('https://example.com/sound.mp3', urls)

    # --- 2. 安全渲染与 XSS 防护 ---
    def test_html_safe_rendering_and_xss(self):
        """测试 HTML 渲染中的 XSS 防御与克林贡语特殊字符"""
        malicious_text = "<script>alert('xss')</script> & Klingon: <Duj legh suvwI'>"
        rendered = self.dumper._render_content_safely(malicious_text)
        self.assertNotIn('<script>', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('&amp;', rendered)

    def test_safe_rendering_audio_control(self):
        """测试音频链接被替换为 HTML5 audio 控件"""
        text = "Listen: https://github.com/user-attachments/assets/audio-1"
        self.dumper.downloaded_media['https://github.com/user-attachments/assets/audio-1'] = 'media/audio.mp3'
        rendered = self.dumper._render_content_safely(text)
        self.assertIn('<audio controls src="media/audio.mp3"></audio>', rendered)

    def test_safe_rendering_video_control(self):
        """测试视频链接被替换为 HTML5 video 控件"""
        text = "Watch: https://github.com/user-attachments/assets/video-1"
        self.dumper.downloaded_media['https://github.com/user-attachments/assets/video-1'] = 'media/video.mp4'
        rendered = self.dumper._render_content_safely(text)
        self.assertIn('<video controls src="media/video.mp4"></video>', rendered)

    def test_safe_rendering_image_box(self):
        """测试图片链接被替换为带有懒加载的图片标签"""
        text = "Photo: https://github.com/user-attachments/assets/img-1"
        self.dumper.downloaded_media['https://github.com/user-attachments/assets/img-1'] = 'media/photo.png'
        rendered = self.dumper._render_content_safely(text)
        self.assertIn('<img src="media/photo.png" loading="lazy" />', rendered)

    def test_safe_rendering_empty_text(self):
        """测试空文本安全渲染默认占位符"""
        self.assertEqual(self.dumper._render_content_safely(''), '<i>无描述内容</i>')
        self.assertEqual(self.dumper._render_content_safely(None), '<i>无描述内容</i>')

    # --- 3. API 请求与分页边界测试 ---
    def test_api_get_empty_first_page(self):
        """测试第一页响应为空时立即正常退出（绝不产生死循环）"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'[]'
        mock_resp.__enter__.return_value = mock_resp

        with patch('urllib.request.urlopen', return_value=mock_resp) as mock_urlopen:
            res = self.dumper._api_get('issues')
            self.assertEqual(res, [])
            self.assertEqual(mock_urlopen.call_count, 1)

    def test_api_get_exact_100_items_boundary(self):
        """测试恰好100条记录时（第1页100条，第2页0条），正常终止且返回全部100条"""
        page1_data = [{'id': i} for i in range(100)]
        page2_data = []

        mock_resp1 = MagicMock()
        mock_resp1.read.return_value = json.dumps(page1_data).encode('utf-8')
        mock_resp1.__enter__.return_value = mock_resp1

        mock_resp2 = MagicMock()
        mock_resp2.read.return_value = json.dumps(page2_data).encode('utf-8')
        mock_resp2.__enter__.return_value = mock_resp2

        with patch('urllib.request.urlopen', side_effect=[mock_resp1, mock_resp2]) as mock_urlopen:
            res = self.dumper._api_get('issues')
            self.assertEqual(len(res), 100)
            self.assertEqual(mock_urlopen.call_count, 2)

    def test_api_get_partial_page(self):
        """测试第1页不足100条时，立即终止并返回结果"""
        partial_data = [{'id': i} for i in range(42)]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(partial_data).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp

        with patch('urllib.request.urlopen', return_value=mock_resp) as mock_urlopen:
            res = self.dumper._api_get('issues')
            self.assertEqual(len(res), 42)
            self.assertEqual(mock_urlopen.call_count, 1)

    def test_api_get_dict_response(self):
        """测试API返回单对象字典（如 /repos/{repo}）时的封装"""
        dict_data = {'id': 12345, 'name': 'test-repo'}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(dict_data).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp

        with patch('urllib.request.urlopen', return_value=mock_resp):
            res = self.dumper._api_get('single_endpoint')
            self.assertEqual(res, [dict_data])

    def test_api_get_network_retry_and_recovery(self):
        """测试遇到瞬态网络异常时的重试恢复逻辑"""
        valid_data = [{'id': 1}]
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(valid_data).encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp

        with patch('urllib.request.urlopen', side_effect=[Exception("Connection reset"), mock_resp]), patch('time.sleep'):
            res = self.dumper._api_get('issues')
            self.assertEqual(res, valid_data)

    # --- 4. 多媒体下载、分块写入与去重 ---
    def test_download_media_caching(self):
        """测试相同 URL 重复下载时直接命中缓存"""
        self.dumper.downloaded_media['https://test.com/file.png'] = 'media/cached.png'
        res = self.dumper._download_media('https://test.com/file.png')
        self.assertEqual(res, 'media/cached.png')

    def test_download_media_chunked_stream(self):
        """测试流式分块下载写入磁盘与哈希一致性"""
        payload = b"A" * (128 * 1024)
        mock_resp = MagicMock()
        mock_resp.geturl.return_value = 'https://github.com/user-attachments/assets/test.pdf'
        mock_resp.headers = {'Content-Type': 'application/pdf', 'Content-Disposition': 'filename="doc.pdf"'}
        mock_resp.read.side_effect = [payload[:64*1024], payload[64*1024:], b'']
        mock_resp.__enter__.return_value = mock_resp

        with patch('urllib.request.urlopen', return_value=mock_resp):
            local_rel = self.dumper._download_media('https://github.com/user-attachments/assets/test.pdf')
            target_path = Path(self.test_dir) / local_rel
            self.assertTrue(target_path.exists())
            self.assertEqual(target_path.stat().st_size, len(payload))
            self.assertEqual(hashlib.sha256(target_path.read_bytes()).digest(), hashlib.sha256(payload).digest())

    def test_download_media_fallback_on_failure(self):
        """测试下载彻底失败时返回原始URL而非崩溃"""
        with patch('urllib.request.urlopen', side_effect=Exception("404 Not Found")), patch('time.sleep'):
            res = self.dumper._download_media('https://broken.link/asset.jpg', max_retries=1)
            self.assertEqual(res, 'https://broken.link/asset.jpg')

    # --- 5. 双重视图生成与结构验证 ---
    def test_view_generation_files_created(self):
        """测试 index.html 与 SUMMARY.md 双重视图文件正确创建"""
        sample_issues = [{'number': 60, 'title': 'Test', 'state': 'open', 'is_pull_request': False, 'author': 'tester', 'created_at': '2026-01-01', 'body': 'body', 'comments': []}]
        sample_releases = [{'id': 1, 'tag_name': 'v1', 'name': 'V1', 'author': 'tester', 'published_at': '2026-01-01', 'body': 'body', 'assets': []}]

        self.dumper._generate_html(sample_issues, sample_releases)
        self.dumper._generate_markdown_summary(sample_issues, sample_releases)

        self.assertTrue((Path(self.test_dir) / 'index.html').exists())
        self.assertTrue((Path(self.test_dir) / 'SUMMARY.md').exists())

    def test_markdown_summary_badges(self):
        """测试 SUMMARY.md 中正确渲染 OPEN/CLOSED 状态徽章"""
        sample_issues = [
            {'number': 1, 'title': 'Open Task', 'state': 'open', 'is_pull_request': False, 'author': 'alice', 'comments': []},
            {'number': 2, 'title': 'Closed PR', 'state': 'closed', 'is_pull_request': True, 'author': 'bob', 'comments': []}
        ]
        self.dumper._generate_markdown_summary(sample_issues, [])
        content = (Path(self.test_dir) / 'SUMMARY.md').read_text(encoding='utf-8')
        self.assertIn('🟢 OPEN', content)
        self.assertIn('🟣 CLOSED', content)
        self.assertIn('PR', content)
        self.assertIn('Issue', content)

    def test_html_view_has_theme_and_container(self):
        """测试生成的 HTML 页面拥有深色主题与主体容器"""
        self.dumper._generate_html([], [])
        content = (Path(self.test_dir) / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<!DOCTYPE html>', content)
        self.assertIn('#0d1117', content)
        self.assertIn('container', content)

    # --- 6. 统计字段与初始化参数 ---
    def test_initial_statistics(self):
        """测试初始统计字段完整性"""
        stats = self.dumper.stats
        self.assertEqual(stats['issues_count'], 0)
        self.assertEqual(stats['prs_count'], 0)
        self.assertEqual(stats['releases_count'], 0)
        self.assertEqual(stats['comments_count'], 0)
        self.assertEqual(stats['media_count'], 0)
        self.assertEqual(stats['media_bytes'], 0)

    def test_custom_token_initialization(self):
        """测试自定义 GITHUB_TOKEN 初始化正确传入请求头"""
        custom_dumper = RepoDumper(repo='test/repo', output_dir=self.test_dir, token='ghp_custom_token_123')
        self.assertEqual(custom_dumper.headers['Authorization'], 'Bearer ghp_custom_token_123')

    def test_repo_name_strip(self):
        """测试仓库名带有空格时被自动截除"""
        d = RepoDumper(repo='  owner/repo  ', output_dir=self.test_dir)
        self.assertEqual(d.repo, 'owner/repo')

    def test_media_dir_auto_creation(self):
        """测试初始化时媒体文件夹被自动创建"""
        self.assertTrue((Path(self.test_dir) / 'media').is_dir())

    def test_release_assets_counting(self):
        """测试 Releases 包含 assets 时的结构与统计计数"""
        sample_rel = [{
            'id': 100,
            'tag_name': 'v2.0',
            'name': 'Version 2.0',
            'author': 'dev',
            'published_at': '2026-09-01',
            'body': 'Release',
            'assets': [{'name': 'bin.zip', 'size': 1024, 'download_count': 5, 'browser_download_url': 'https://download/bin.zip'}]
        }]
        self.dumper._generate_markdown_summary([], sample_rel)
        content = (Path(self.test_dir) / 'SUMMARY.md').read_text(encoding='utf-8')
        self.assertIn('`v2.0`', content)
        self.assertIn('1 个', content)

if __name__ == '__main__':
    unittest.main()
