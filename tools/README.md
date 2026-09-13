# 📦 仓库全量数据与多媒体归档工具 (Repository Dump Tool)

专为 `THE-ERROR-IS-THE-MESSAGE` 仓库及任意 GitHub 仓库设计的高性能、零依赖全量导出工具。

## ✨ 核心特性

1. **零第三方依赖**：纯 Python 3 标准库（`urllib`, `json`, `hashlib`, `re`），开箱即跑，无需 `pip install` 任何额外包。
2. **多媒体与附件深度抓取**：
   - 自动解析并下载所有通过 GitHub 上传的媒体：图片（`.png`, `.jpg`）、音频（`.mp3`, `.wav`）、视频（`.mp4`）及 PDF 附件；
   - 基于内容与 URL 计算 SHA-256 校验和与防重名哈希；
   - 自动推断并修正 MIME 类型与文件后缀。
3. **离线可视化网页 (`index.html`)**：
   - 自动生成独立的暗黑模式响应式离线浏览页面；
   - 原生支持内嵌图片展示与音频播放器控件；
   - 完整保留克林贡语（Klingon）、Emoji 及多国语言文本。
4. **移动端一键触发**：
   - 配置了 `.github/workflows/archive.yml`；
   - 支持在手机端 GitHub App 中进入 **Actions -> Repository History & Media Archive -> Run workflow** 一键离线导出并下载 Artifact 打包。

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
