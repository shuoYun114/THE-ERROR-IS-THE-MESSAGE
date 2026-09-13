# 📦 仓库全量数据与多媒体归档工具 (Repository Dump Tool)

专为 `THE-ERROR-IS-THE-MESSAGE` 仓库及任意 GitHub 仓库设计的高性能、零依赖全量导出工具。

## ✨ 核心特性（100% 对齐官方 SPEC）

1. **三大核心数据区全量覆盖 (Complete Extraction Across 3 Main Areas)**：
   - **Issues**：所有 open / closed 工单、完整描述与全部讨论评论线程；
   - **Pull Requests (PRs)**：所有合并请求、修改描述与 review 讨论；
   - **Releases**：所有版本发布说明、版本标签（Tags）与 Release Assets 二进制资产文件。
2. **多媒体与附件深度抓取 (三重探测引擎)**：
   - 自动解析并下载所有 GitHub 附件：图片（`.png`, `.jpg`）、音频（`.mp3`, `.wav`）、视频（`.mp4`）、PDF 规范文档及 Release 资产；
   - 结合 `Content-Disposition` 响应头、S3 重定向解析与哈希校验，**100% 精确保留真实语义文件名**；
   - 采用 **64KB 分块流式读取与 3 次网络断线自动重试**，杜绝大文件内存暴涨与 OOM。
3. **支持直存仓库与多端持久化 (Direct Repo Dump & Usability)**：
   - 自动支持将全量导出的 `dump/` 数据直接自动 Commit & Push 回仓库独立的 `archive` 分支；
   - 同时上传为 GitHub Actions Artifact 打包供下载，双保险持久化保存。
4. **双重视图可视化支持**：
   - **离线响应式网页 (`index.html`)**：全量 XSS 安全转义，暗黑主题，内嵌原生 HTML5 音视频播放器，无损呈现克林贡语（Klingon）与 Emoji；
   - **GitHub 原生视图 (`SUMMARY.md`)**：自动生成结构化 Issue、PR 与 Release 清单，无需解压即可在 GitHub 仓库直观预览。
5. **移动端一键触发**：
   - 支持在手机端 GitHub App 中进入 **Actions -> 📦 Repository History & Media Archive -> Run workflow** 一键离线导出。

---

## 🚀 使用方法

### 本地直接运行：

```bash
# 运行单元测试套件：
python -m unittest discover tests

# 执行全量仓库与媒体归档：python tools/repo_dump.py <owner/repo> [output_dir]
python tools/repo_dump.py "attogram/THE-ERROR-IS-THE-MESSAGE" "dump"
```

如果配置了环境变量 `GITHUB_TOKEN` 或已通过 `gh auth login`，脚本将自动获取授权，享受每小时 5000 次的高速 API 额度。

### 移动端 / GitHub Actions 触发：
1. 打开移动端 GitHub App 或网页，访问仓库的 **Actions** 标签页；
2. 选择 **📦 Repository History & Media Archive** 工作流；
3. 点击 **Run workflow**，系统将全自动完成抓取、生成 Summary、上传打包文件并提交至 `archive` 分支。
