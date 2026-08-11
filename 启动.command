#!/bin/bash
# ============================================================
#  🎙️  Auto Transcriber & Speaker Diarization
#  双击此文件启动应用
# ============================================================

# 切换到项目目录
cd "$(dirname "$0")" || cd /Users/ruikairen/Desktop/transcript

# 设置环境变量（确保 ffmpeg 可用）
export PATH="/opt/homebrew/bin:$PATH"

# 激活虚拟环境（如果存在）
if [ -f venv/bin/activate ]; then
    source venv/bin/activate
fi

# 启动应用
python3 app.py &

# 等待服务就绪后自动打开浏览器
sleep 3
open http://127.0.0.1:7860
