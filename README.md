# Auto Transcriber & Annotation Tool

基于 Gradio 的视频自动转写与标注工具，集成了语音识别（Whisper）、说话人分离（pyannote.audio）、标点恢复、句子拆分等功能，并提供 **ELAN 风格多层时间轴编辑器**，支持拖拽调整、层归属管理、撤销/重做，最终导出标准的 EAF 和 PFSX 标注文件。

适用于语言学、教育学、儿童语言研究等需要高质量语音转写与标注的场景。

---

## 主要功能

### 自动转写流水线
- **语音识别** — faster-whisper（支持 tiny / base / small / medium / large 模型）
- **说话人分离** — pyannote.audio 3.1，可选指定说话人数量或自动检测
- **时间对齐** — 将 ASR 结果与说话人分离结果精确对齐
- **标点恢复** — FunASR ct-punc 模型，自动添加句号、逗号、问号等
- **中文分词** — jieba 分词，每个词附带时间戳
- **句子拆分** — 按标点自动拆分长段为独立句子

### 模板 EAF 支持
- 上传已有 EAF 作为模板，自动解析层级结构（linguistic type、tier 顺序、PARTICIPANT）
- 支持 **PARENT_REF** 父子层级关系（如 `interaction@INB` 自动识别为 `INB` 的子层）
- 导出的 EAF 保留模板的层级结构和属性

### 说话人映射
- 为每个检测到的说话人生成代表性音频片段（可在线播放）
- 查看说话人的转写样本，辅助判断身份
- 将说话人映射到模板 EAF 中的对应层名

### 🖱️ 时间轴编辑器（核心人工核查工具）

在自动转写完成后，进入 Step 4 时间轴编辑器，你可以：

- **拖拽调整时间**：直接拖拽 segment 的左右边缘或整体移动，弥补自动识别的时间偏差
- **修改文本内容**：单击选中后直接在编辑面板中修改转写文本
- **调整层归属**：通过直接拖拽或下拉菜单将 segment 归到正确的说话人层
- **添加/删除 segment**：双击空白区域添加新标注，或删除误标条目
- **一键分词**：选中文本后点击分词，自动标准化格式
- **撤销/重做**：支持 Ctrl+Z / Ctrl+Shift+Z，操作失误随时回退

**核心价值**：你不需要逐句手动转写，只需在自动生成的基础上进行核查和微调，**将精力集中在儿童的行为表征判断上**，而非机械的听写工作。

### 导出
- **EAF** — ELAN Annotation Format，可在 [ELAN](https://archive.mpi.nl/tla/elan) 中打开
- **PFSX** — Phon Session XML，配套文件
- 支持**自定义输出目录**

---


### "自动转写 + 人工核查"工作流

| 对比项 | 传统 ELAN 手动转写 | 本工具（自动 + 人工核查） |
|--------|-------------------|--------------------------|
| 转写效率 | 10分钟视频需 4-6 小时 | 1 小时视频约 40-70 分钟 |
| 操作方式 | 逐句听写 + 手动创建层 | 自动生成初稿 + 拖拽微调 |
| 学习成本 | 需掌握 ELAN 复杂功能 | 网页操作，直观易用 |
| 专注点 | 被技术细节分心 | **专注于儿童行为表征** |
| 输出格式 | EAF | EAF（完全兼容 ELAN） |

---

## 系统要求

| 组件 | 要求 |
|------|------|
| 操作系统 | macOS（推荐）/ Linux / Windows |
| Python | ≥ 3.9 |
| FFmpeg | 必须安装，且可在命令行调用 |
| 磁盘空间 | 约 10–20 GB（用于模型缓存，首次运行自动下载） |
| 内存 | 建议 8 GB+ |

### GPU 加速（可选）

如果安装了 CUDA 版 PyTorch，系统会自动使用 GPU 加速转写（约快 3–5 倍）。

---

## 安装与配置

### 1. 获取代码

```bash
cd /path/to/transcript
```

### 2. 创建虚拟环境（推荐）

```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

> **注意**：`pyannote.audio` 依赖 PyTorch。如需 GPU 加速，请先安装对应 CUDA 版本的 PyTorch（参见 [pytorch.org](https://pytorch.org)）。

### 4. 安装 FFmpeg

| 平台 | 命令 |
|------|------|
| macOS (Homebrew) | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt install ffmpeg` |
| Windows (Scoop) | `scoop install ffmpeg` |
| Windows (Chocolatey) | `choco install ffmpeg` |

验证安装：`ffmpeg -version`

### 5. 配置 Hugging Face Token

`pyannote.audio` 模型需要 Hugging Face 访问令牌。

1. 在 [hf.co/settings/tokens](https://huggingface.co/settings/tokens) 创建 token
2. 接受模型许可协议：
   - [pyannote/speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1)
   - [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)
3. 在项目根目录创建 `.env` 文件：

```bash
echo 'HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx' > .env
```

> `.env` 已加入 `.gitignore`，不会被提交到 Git。

---

## 使用指南

### 启动应用

```bash
python3 app.py
```

浏览器打开 **http://127.0.0.1:7860**。

macOS 用户也可双击桌面上的 `启动转录工具.command` 一键启动。

### 完整工作流

#### Step 1 — 上传与处理

1. 上传 MP4 视频文件
2. （可选）上传 EAF 模板文件（用于预定义层级结构）
3. （可选）指定说话人数量（留空则自动检测）
4. 设置输出目录（默认 `~/Desktop`）
5. 点击 **"开始处理"**

处理进度会实时显示，包括音频提取、转写、说话人分离、对齐等阶段。

#### Step 2 — 处理完成

查看处理结果摘要：检测到的说话人数量、转写段数等。

点击 **"进入映射"** 继续。

#### Step 3 — 说话人映射

- 🎧 点击每个说话人的音频播放器，听取声音样本
- 📖 查看该说话人的转写文本样本
- 🏷️ 在下拉框中选择对应的层名（来自模板 EAF）或输入自定义名称
  - 选 `xxx` 表示该说话人为儿童
  - 留空则保留默认说话人 ID

点击 **"进入时间轴编辑"** 继续。

#### Step 4 — 时间轴编辑器（人工核查阶段）

这是核心的人工核查界面。算法已自动生成初稿，你只需核查和微调：

- **拖拽调整**：发现自动识别的时间偏差，直接拖拽 segment 边缘或整体移动修正
- **修改文本**：转写错误直接在右侧面板编辑，修改立即生效
- **调整层归属**：说话人分配错误，下拉菜单一键切换到正确层
- **添加/删除**：补充漏标（双击空白区域）或删除误标
- **一键分词**：快速标准化中文文本格式
- **撤销/重做**：Ctrl+Z / Ctrl+Shift+Z，大胆修改，随时回退

> **💡 专注儿童行为**：让算法处理粗活，让你专注于行为观察与判断。

#### Step 5 — 导出

点击 **"📦 应用修改并生成 EAF"**，文件将生成到指定的输出目录：

- `output.eaf` — ELAN 标注文件
- `output.pfsx` — Phon Session XML 文件

在 Step 5 页面可直接下载。

---

## 配置参考

### `.env` 文件

```
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxx
```

也可设置环境变量：

```bash
export HF_TOKEN="hf_xxxxxxxxxxxxxxxxxxxx"
```

### `config.py` 主要参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `WHISPER_MODEL` | `small` | Whisper 模型大小（tiny/base/small/medium/large） |
| `SAMPLE_RATE` | 16000 | 音频采样率 |
| `MAX_SPEAKERS` | 10 | 最大说话人数 |
| `CLIP_DURATION` | 5.0 | 说话人样本音频时长（秒） |
| `DEVICE` | 自动检测 | `cuda` 或 `cpu` |
| `TEMP_DIR` | `./temp` | 临时文件目录 |

---

## 目录结构

```
transcript/
├── app.py                         # Gradio 主应用（UI + 路由）
├── timeline_editor.py             # ELAN 风格多层时间轴编辑器（完整HTML/CSS/JS）
├── config.py                      # 全局配置（模型、路径、音频参数）
├── requirements.txt               # Python 依赖
├── .env                           # HuggingFace Token（不纳入版本控制）
├── .gitignore
├── README.md
├── generate_test_video.py         # 测试视频生成脚本
├── test_pipeline.py               # 流水线测试
├── processors/
│   ├── __init__.py                # 流水线编排器
│   ├── audio_extractor.py         # ffmpeg 音频提取
│   ├── audio_utils.py             # 音频工具函数
│   ├── transcriber.py             # faster-whisper 语音识别
│   ├── diarizer.py                # pyannote.audio 说话人分离
│   ├── aligner.py                 # 时间对齐 + 说话人音频片段
│   ├── punctuation.py             # FunASR 标点恢复 + 中文分词
│   └── exporter.py                # EAF + PFSX 文件生成
├── temp/                          # 临时文件（自动清理）
├── templates/                     # EAF 模板持久化存储
├── logs/                          # 错误日志
└── output/                        # 导出文件默认目录
```

---

## 常见问题

### Q: 端口 7860 被占用

```bash
# 查找并终止占用进程
lsof -ti:7860 | xargs kill -9
# 然后重新启动
python3 app.py
```

### Q: `ffmpeg: command not found`

请确认 FFmpeg 已正确安装并加入 PATH。运行 `ffmpeg -version` 验证。

### Q: `HF_TOKEN is required`

1. 确认 `.env` 文件存在于项目根目录
2. 确认已接受 pyannote 模型许可协议
3. 确认 token 格式正确（以 `hf_` 开头）

### Q: 模板 EAF 解析失败

- 确认 EAF 文件格式正确（可用 ELAN 打开验证）
- 检查模板中的 tier 是否包含 PARENT_REF 属性
- 对于 `@` 命名约定的层级（如 `interaction@INB`），系统会自动识别父子关系

### Q: 导出时没有包含手动修改

- 确保在 Step 4 中做了实际修改后**立即看到 segment 更新**（无需手动保存）
- 打开浏览器开发者工具（F12 → Console），确认每次编辑后出现 `[syncToParent]` 日志
- 如果看到 `Using FALLBACK data` 警告，说明数据未成功同步，请刷新页面重试

### Q: 如何升级 Whisper 模型

修改 `config.py` 中的 `WHISPER_MODEL` 或设置环境变量：

```bash
export WHISPER_MODEL="medium"
python3 app.py
```

模型大小与性能对比：

| 模型 | 速度 | 准确率 | 显存（GPU） |
|------|------|--------|-------------|
| tiny | 最快 | 较低 | ~1 GB |
| base | 快 | 一般 | ~1 GB |
| small | 中等 | 较好 | ~2 GB |
| medium | 慢 | 好 | ~5 GB |
| large | 最慢 | 最好 | ~10 GB |

### Q: `CUDA out of memory`

使用更小的 Whisper 模型：`export WHISPER_MODEL=base`

或在 CPU 上运行（会自动检测并使用 CPU）。

### Q: 如何添加自定义层

在 Step 4 的控制面板中，使用 "Add Tier" 输入框添加新层名，或在 segment 编辑面板的层下拉框中直接输入新名称。

---

## 贡献指南

欢迎提交 Issue 和 Pull Request。

1. Fork 本仓库
2. 创建特性分支：`git checkout -b feature/your-feature`
3. 提交改动：`git commit -m "feat: 描述"`
4. 推送到分支：`git push origin feature/your-feature`
5. 创建 Pull Request

---

## 致谢

本项目基于以下优秀的开源项目构建：

| 项目 | 用途 |
|------|------|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | 语音识别 |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) | 说话人分离 |
| [FunASR](https://github.com/modelscope/FunASR) | 标点恢复 |
| [Gradio](https://github.com/gradio-app/gradio) | Web UI 框架 |
| [pympi-ling](https://github.com/dopefishh/pympi) | EAF/PFSX 文件读写 |
| [jieba](https://github.com/fxsjy/jieba) | 中文分词 |
| [librosa](https://github.com/librosa/librosa) | 音频处理 |

---

## 许可证

本项目仅供研究和教育用途使用。
