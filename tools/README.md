# 📦 仓库全量数据与多媒体归档工具 (Repository Dump Tool)

专为 `THE-ERROR-IS-THE-MESSAGE` 仓库及任意 GitHub 仓库设计的高性能、零依赖全量导出工具。

## ✨ 核心特性

1. **零第三方依赖**：纯 Python 3 标准库（`urllib`, `json`, `hashlib`, `re`, `html`），开箱即跑，无需 `pip install` 任何额外包。
2. **多媒体与附件深度抓取 (三重探测引擎)**：
   - 自动解析并下载所有通过 GitHub 上传的媒体：图片（`.png`, `.jpg`）、音频（`.mp3`, `.wav`）、视频（`.mp4`）及 PDF 附件；
   - 结合 `Content-Disposition` 响应头、S3 重定向解析与哈希校验，**100% 精确保留真实文件名**；
   - 采用 **64KB 分块流式读取与 3 次网络断线自动重试**，杜绝大文件内存暴涨与 OOM。
3. **双重视图可视化支持**：
   - **离线响应式网页 (`index.html`)**：全量 XSS 安全转义，暗黑主题，内嵌原生 HTML5 音频/视频播放器，无损呈现克林贡语（Klingon）与 Emoji；
   - **GitHub 原生视图 (`SUMMARY.md`)**：自动生成结构化 Issue 与附件清单，无需解压即可在 GitHub 仓库直观预览。
4. **移动端一键触发**：
   - 配置了 `.github/workflows/archive.yml`；
   - 支持在手机端 GitHub App 中进入 **Actions -> 📦 Repository History & Media Archive -> Run workflow** 一键离线导出并下载 Artifact 打包。

---

## 🚀 使用方法

### 本地直接运行：

```bash
# 基本用法：python tools/repo_dump.py <owner/repo> [output_dir]
python tools/repo_dump.py "attogram/THE-ERROR-IS-THE-MESSAGE" "dump"
```

如果配置了环境变量 `GITHUB_TOKEN` 或已通过 `gh auth login`，脚本将自动获取授权，享受每小时 5000 次的高速 API 额度。

### 移动端 / GitHub Actions 触发：
1. 访问仓库的 **Actions** 标签页；
2. 选择 **📦 Repository History & Media Archive** 工作流；
3. 点击 **Run workflow** 即可在云端全自动执行导出并打包为 Artifact。
