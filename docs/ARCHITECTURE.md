# 🏛️ MuseWave Studio · 灵音工坊 V1.1 — 完整架构、精准时长控制与全链路成本核算白皮书 (Architecture & Cost Analytics Guide)

---

## 1. 标准工程目录结构 (Project Structure)

```text
music-studio/
├── main.py                        # 应用启动入口 (CLI & FastAPI ASGI Target)
├── deploy.sh                      # Cloud Build + Cloud Run 一键云端构建与部署脚本 (本机免装 Docker)
├── deploy_cloud_run.sh            # deploy.sh 的兼容别名脚本
├── cloudbuild.yaml                # Google Cloud Build 原生全链路云端构建与部署配置
├── Dockerfile                     # 供云端 Cloud Build 使用的容器构建描述文件 (python:3.11-slim + ffmpeg + fonts-noto-cjk)
├── .dockerignore                  # Docker 构建上下文忽略规则
├── .gcloudignore                  # gcloud builds submit 上传源码包忽略规则
├── .gitignore                     # Git 忽略规则
├── requirements.txt               # Python 依赖清单
├── README.md                      # 项目总览与快速导航 (中英双语)
├── music_studio_app.py            # 核心后端引擎 (Gemini 4 声学分析 / Lyria 3 编曲与自愈生成 / 盲测裁判 / 成本核算引擎)
├── music_studio_ui.html           # 全链路双语前端工作台 (含 💰 全链路成本看板与 CSV 归档下载)
└── docs/
    ├── QUICKSTART.md              # 快速上手与 Cloud Build + Cloud Run 部署指南 (4 大场景 + 权限排查)
    └── ARCHITECTURE.md            # 本手册：系统架构、精准时长控制、自愈重试与全链路成本核算公式白皮书
```

---

## 2. 专属服务账号与最小权限模型 (`music-studio-sa`)

与 `speaker-diarization-studio` 完全对齐，平台不依赖高危的 Compute Engine 默认服务账号，而是由 [`deploy.sh`](../deploy.sh) 自动创建并绑定专属最小权限服务账号 **`music-studio-sa@${PROJECT_ID}.iam.gserviceaccount.com`**：

| GCP 控制台显示名称 | IAM Role ID | 权限用途 |
| :--- | :--- | :--- |
| **Agent Platform User** | `roles/aiplatform.user` | 调用 Vertex AI `gemini-4-argon` 与 `lyria-3-pro-preview` / `lyria-3-clip-preview` |
| **Logs Writer** | `roles/logging.logWriter` | 向 Cloud Logging 输出结构化 JSON 追踪日志 |
| **Service Usage Consumer** | `roles/serviceusage.serviceUsageConsumer` | 允许服务账号使用项目级 Google Cloud API 配额 |
| **Storage Object User** | `roles/storage.objectUser` | 读写 GCS Bucket 中的生成音频、评测报告与成本快照归档 |

---

## 3. 全链路成本核算引擎 (`CostAnalyticsEngine`) 与计费公式

平台内置实时与离线双模成本核算引擎（[`CostAnalyticsEngine`](../music_studio_app.py)），覆盖从 **Gemini 4 原曲分析 $\rightarrow$ Gemini 4 编曲编译 $\rightarrow$ Lyria 3 音频合成（含自愈重试） $\rightarrow$ Gemini 4 盲测打分 $\rightarrow$ GCS 存储归档** 的全链路成本，支持按组（`A组` / `B组` / `C组` / `U组`）聚合与一键导出带时间戳的 CSV/JSON 报表。

### 单首歌曲总成本公式
$$\text{Total Song Cost (USD)} = C_{\text{Stage1: Analysis}} + C_{\text{Stage2: Compiler}} + C_{\text{Stage3: Lyria}} + C_{\text{Stage4: Judge}} + C_{\text{Stage5: GCS}}$$

1. **阶段 1：Gemini 4 原曲多模态分析成本 ($C_{\text{Stage1}}$)**
   $$T_{\text{in,1}} = \text{ round}(D_{\text{ref\_sec}} \times 32) + T_{\text{sys\_prompt,1}}$$
   $$C_{\text{Stage1}} = \frac{T_{\text{in,1}}}{10^6} \times P_{\text{in}} + \frac{T_{\text{think,1}}}{10^6} \times P_{\text{think}} + \frac{T_{\text{out,1}}}{10^6} \times P_{\text{out}}$$
2. **阶段 2：Gemini 4 $\rightarrow$ Lyria 3 编曲结构编译成本 ($C_{\text{Stage2}}$)**
   - 若关闭编译器（透传模式）：$C_{\text{Stage2}} = 0$。
   - 若开启编译器：按实际 `usage_metadata`（或提示词字符数折算 Token）计算输入、Thinking 与 JSON 编曲输出 Token 成本。
3. **阶段 3：Lyria 3 音乐生成成本 ($C_{\text{Stage3}}$)**
   - **按次计费模式 (`per_track`，默认官方标准)**：
     - `lyria-3-pro-preview`：`$0.080 / 次`
     - `lyria-3-clip-preview`：`$0.040 / 次`
   - **按秒折算模式 (`per_second`，可选)**：按实际生成秒数 $D_{\text{actual\_sec}} \times R_{\text{sec}}$ 计费。
   - 支持开关 `bill_failed_self_healing_attempts` 控制是否将自愈重试的中间失败轮次计入账单。
4. **阶段 4：Gemini 4 多轨盲测裁判成本 ($C_{\text{Stage4}}$)**
   - 仅当执行了多轨横向盲测打分时产生，包含多轨音频 Token（$\sum D_i \times 32$）与裁判 JSON 报告输出成本。
5. **阶段 5：GCS 云存储与请求成本 ($C_{\text{Stage5}}$)**
   - 默认按 `$0.0002 / 首` 核算 `PUT/GET` 与持久化存储开销。

---

## 4. Lyria 3 精准时长控制与 6 层防截断架构

针对长音乐生成中常见的“提前收尾”或“尾音硬截断”问题，平台实现了 6 层全链路防护：
1. **显式时长与 BPM 小节对齐计算**：根据目标秒数 $T_{\text{target}}$ 与 BPM 自动计算总小节数（Bars），按 `Intro -> Verse -> Pre-Chorus -> Chorus -> Bridge -> Final Chorus -> Outro` 分配精确时间窗。
2. **Gemini 4 底层增强编译器（公开音乐学规则）**：100% 基于公开音乐理论与声学编曲结构，将简短描述扩展为带分段时间戳与器乐动态演进的 `SongPlan`（零内部敏感信息）。
3. **8 秒自然尾奏缓冲带（Outro Cadence Buffer）**：在 `[T - 8s, T]` 强制注入 `Outro / Fade-out Cadence` 音乐收束指令，防止在最高潮（Chorus）处戛然而止。
4. **3 阶自愈重试引擎（3-Stage Self-Healing Retry）**：当检测到返回音频为空或异常截断时，自动在 `Stage 1 (精简提示词) -> Stage 2 (纯净分段结构) -> Stage 3 (高稳定人声恢复)` 间无感降级重试。
5. **能量包络探测与精确裁剪（Energy-Aware Tail Align）**：若模型生成的音频超出目标秒数，自动在目标秒数附近的低能量谷值处平滑裁剪并施加 `afade` 渐弱包络。
6. **工业级 `-14.0 LUFS` 响度标准化（可选）**：支持一键开启 `ffmpeg loudnorm=I=-14:TP=-1.0:LRA=11` 流媒体母带级响度对齐。
