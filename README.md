# 🎼 MuseWave Studio · 灵音工坊 / Music Creation & Acoustic Intelligence (V1.1)

基于 **原生全音频多模态直析与编曲引擎 (`gemini-4-argon`, `thinking_level="MEDIUM"`, `audio_timestamp=True`)** 与 **Google Lyria 3 (`lyria-3-pro-preview` / `lyria-3-clip-preview`)** 构建的企业级音乐理解、逆向乐谱拆解、结构化提示词工程、精准时长控制、多模型盲测打分（Multi-Track Blind Judge）与 **全链路逐首/按组成本核算看板 (`CostAnalyticsEngine`)** 平台，支持 **全栈中英双语（`中文` / `EN` / `双语 Bi`）无缝切换** 与 **Google Cloud Storage (`gs://`) 动态流式加载与自动归档**。

Enterprise-grade Music Acoustic Analysis, Reverse Sheet Music & Stem Decomposition, Structured Prompt Compilation, Precision Duration Alignment, Multi-Track Blind Judging, and **Full-Chain Per-Song & Per-Group Cost Analytics (`CostAnalyticsEngine`)** web application powered by **direct multimodal audio understanding (`gemini-4-argon`, `thinking_level="MEDIUM"`, `audio_timestamp=True`)** and **Google Lyria 3 (`lyria-3-pro-preview`)**, with **full Chinese-English bilingual (`zh` / `en` / `Bilingual Side-by-Side`) support**.

---

## 📚 快速导航 (Documentation)

1. **[⚡ 快速上手与 Cloud Run 部署指南 (`docs/QUICKSTART.md`)](./docs/QUICKSTART.md)**
   - 包含 **本地 30 秒直接启动**、**3 种 Cloud Run 一键安全部署模式**（零明文 SHA-256 口令门禁模式 / `gcloud proxy` 零信任私密隧道 / 企业级原生 IAP 白名单）以及 **IAM 权限快速排查手册**。
2. **[🏛️ 完整系统架构、精准时长控制与全链路成本核算白皮书 (`docs/ARCHITECTURE.md`)](./docs/ARCHITECTURE.md)**
   - 包含 **标准工程目录结构**、**专属服务账号 (`music-studio-sa`) 最小权限列表**、**5 阶段全链路成本计算公式与 CSV/JSON 自动归档机制**，以及 **Lyria 3 6 层精准时长控制与 3 阶自愈重试架构**。

---

## 🚀 30 秒极速启动 (Quick Start)

### 方式一：本地直接运行 (Local Execution)
```bash
pip install -r requirements.txt
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"   # Replace with your GCP Project ID
python3 main.py --host 0.0.0.0 --port 8080
```
Open in browser: `http://127.0.0.1:8080`

### 方式二：一键安全部署至 Google Cloud Run (Automated Cloud Run Deployment)
```bash
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"   # Replace with your GCP Project ID
export GOOGLE_CLOUD_REGION="us-central1"
export GCS_BUCKET="${GOOGLE_CLOUD_PROJECT}-music-studio"   # Optional: bind default GCS bucket
chmod +x deploy.sh
PROMPT_PASSCODE="true" ./deploy.sh
```

---

## 📦 工程目录结构 (Project Structure)

```text
music-studio/
├── main.py                        # 应用启动入口 (CLI & FastAPI ASGI Target)
├── deploy.sh                      # Cloud Build + Cloud Run 一键云端构建与部署脚本 (本机无需安装 Docker)
├── deploy_cloud_run.sh            # deploy.sh 的兼容别名脚本
├── cloudbuild.yaml                # Google Cloud Build 原生全链路云端构建与部署配置
├── Dockerfile                     # 供云端 Cloud Build 使用的容器构建描述文件 (python:3.11-slim + ffmpeg + fonts-noto-cjk)
├── .dockerignore                  # Docker 构建上下文忽略规则
├── .gcloudignore                  # gcloud builds submit 上传源码包忽略规则
├── .gitignore                     # Git 忽略规则
├── requirements.txt               # Python 依赖清单
├── README.md                      # 项目总览与快速导航 (中英双语)
├── music_studio_app.py            # 核心引擎与路由 (Gemini 4 分析 / Lyria 3 编译器与自愈生成 / 盲测裁判 / CostAnalyticsEngine)
├── music_studio_ui.html           # 直观全链路中英双语前端工作台 (含 💰 全链路成本看板与历史 CSV 归档)
└── docs/
    ├── QUICKSTART.md              # 快速上手与 Cloud Build + Cloud Run 云端部署指南
    └── ARCHITECTURE.md            # 完整架构、权限、全链路成本公式与时长控制白皮书
```
