# ⚡ MuseWave Studio · 灵音工坊 V1.1 — 快速上手与 Cloud Run 部署指南 (Quickstart Guide)

> **💡 文档定位**：本指南专为希望在客户或企业环境中 **快速完成本地启动或 Google Cloud Run 部署** 的用户设计。只需根据您的实际使用场景，按顺序复制执行 3 步命令即可完成部署。
> - **核心能力**：基于 **Gemini 4 原生全音频多模态直析与编曲引擎 (`gemini-4-argon`, `thinking_level="MEDIUM"`, `audio_timestamp=True`)** 与 **Google Lyria 3 (`lyria-3-pro-preview` / `lyria-3-clip-preview`)** 构建的企业级音乐理解、逆向乐谱拆解、结构化提示词工程、精准时长控制（6 层全链路防护）、多模型盲测打分（Multi-Track Blind Judge）与 **全链路逐首/按组成本核算看板 (`CostAnalyticsEngine`)** 平台，支持 **全栈中英双语（`中文` / `EN` / `双语 Bi`）无缝切换** 与 **Google Cloud Storage (`gs://`) 动态流式加载与自动归档**。

---

## 🧭 场景选型建议（先选最适合您的方式）

| 场景选项 | 适用对象 | 是否需要配置 OAuth Consent Screen？ | 访问者是否需安装 `gcloud`？ | 选型建议 |
| :--- | :--- | :--- | :--- | :--- |
| **[场景 A：本地电脑直接运行](#场景-a在本地电脑直接运行30-秒)** | 开发者本机自测 | ❌ **不需要** | 本机需有 Python & `gcloud` | 本地调试首选，30 秒启动。 |
| **[场景 B：Cloud Run + 内置 SHA-256 口令门禁](#场景-b部署到-cloud-run--内置-sha-256-口令门禁推荐外部客户演示免配-oauth)** | **发链接给外部客户或业务方直接浏览器访问** | ❌ **完全不需要配置 OAuth Consent！**<br>*(走应用内置加密口令验证，不依赖 Google 登录)* | ❌ **不需要** | **⭐ 外部演示最省心推荐**：客户点开链接输入口令即可访问（前提是 GCP 项目未禁止 `--allow-unauthenticated`）。 |
| **[场景 C：Cloud Run + `gcloud proxy` 本地安全隧道](#场景-c部署到-cloud-run--gcloud-proxy-安全隧道推荐内部团队)** | 内部技术团队 | ❌ **不需要** | ✅ 需要 | **⭐ 内部评测最安全推荐**：公网完全拒绝访问 (`403`)，零额外配置。 |
| **[场景 D：Cloud Run + 原生 IAP 身份代理](#场景-d部署到-cloud-run--原生-iap-身份代理企业级-google-账号白名单)** | 组织策略禁止公开 URL，且需让客户用浏览器直接登录 | ⚠️ **同组织内部访问不需要**；<br>若允许**外部客户**登录，需将 OAuth Consent Screen 设为 `External` (1分钟) | ❌ **不需要** | **⭐ 企业合规推荐**：通过 Google 账号邮箱白名单控制访问权限。 |

---

## 💻 场景 A：在本地电脑直接运行（30 秒）

不需要 Docker，只要本机有 Python 3.10+ 和 `gcloud` 即可：

### 第 1 步：安装依赖
```bash
pip install -r requirements.txt
```

### 第 2 步：登录 Google Cloud 账号并指定项目
*(请将下方的 `your-gcp-project-id` 替换为您自己的 GCP Project ID)*
```bash
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
```

### 第 3 步：启动网页服务
```bash
python3 main.py --host 0.0.0.0 --port 8080
```
✅ **完成！** 打开浏览器访问：**`http://127.0.0.1:8080`**

---

## 🌐 场景 B：部署到 Cloud Run + 内置 SHA-256 口令门禁（推荐外部客户演示，免配 OAuth）

- **核心优势**：**完全不需要配置 OAuth Consent Screen**，客户也不需要拥有 Google 账号或安装 `gcloud`。客户点开网址后，在内置的加密登录卡片中输入您设定的访问口令即可进入（口令在前端与云端均以不可逆的 `SHA-256` 哈希传输和存储，全程不暴露明文）。

### 第 1 步：设置您的 GCP 项目 ID
*(请将第一行的 `your-gcp-project-id` 替换为您自己的 GCP Project ID)*
```bash
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
export GOOGLE_CLOUD_REGION="us-central1"
gcloud config set project "${GOOGLE_CLOUD_PROJECT}"
```

### 第 2 步：（可选）创建同区域 GCS Bucket 用于加载素材与持久化音乐及成本报告
```bash
export GCS_BUCKET="${GOOGLE_CLOUD_PROJECT}-music-studio"
gcloud storage buckets create "gs://${GCS_BUCKET}" --project="${GOOGLE_CLOUD_PROJECT}" --location="${GOOGLE_CLOUD_REGION}" --uniform-bucket-level-access || true
```

### 第 3 步：一键部署并设置访问口令
```bash
chmod +x deploy.sh
PROMPT_PASSCODE="true" ./deploy.sh
```
- 运行后终端会提示：`🔑 请输入要设置的访问口令:`，直接输入您设定的口令并回车（输入过程完全隐藏不回显）。
- 脚本会自动完成：**启用必需 API → 创建专用服务账号 (`music-studio-sa`) → 绑定 4 项最小权限 (`roles/aiplatform.user` / `roles/serviceusage.serviceUsageConsumer` / `roles/logging.logWriter` / `roles/storage.objectUser`) → 构建镜像 → 部署至 Cloud Run**。
- ✅ **完成！** 部署结束后，将终端打印出的 `https://music-studio-xxxx.us-central1.run.app` 网址与口令发送给客户即可。

---

## 🔒 场景 C：部署到 Cloud Run + `gcloud proxy` 安全隧道（推荐内部团队）

此模式下 Cloud Run 开启 `--no-allow-unauthenticated`（公网直接打开网址会返回 `403 Forbidden`），仅允许授权人员通过本地 IAM 加密隧道访问。

### 第 1 步：设置 GCP 项目 ID 并一键部署
```bash
export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
export GOOGLE_CLOUD_REGION="us-central1"
chmod +x deploy.sh
./deploy.sh
```

### 第 2 步：在本地建立安全访问代理
```bash
gcloud run services proxy music-studio \
  --project="${GOOGLE_CLOUD_PROJECT}" \
  --region="${GOOGLE_CLOUD_REGION}" \
  --port=8080
```
✅ **完成！** 保持终端运行，打开浏览器访问：**`http://127.0.0.1:8080`**

---

## 🛡️ 场景 D：部署到 Cloud Run + 原生 IAP 身份代理（企业级 Google 账号白名单）

如果您的企业 GCP 组织策略禁止开启 `--allow-unauthenticated`，同时希望用户直接用浏览器通过 Google 账号登录访问，推荐使用 Cloud Run 原生 IAP：

1. **第 1 步：开启 IAP 一键部署**
   ```bash
   export GOOGLE_CLOUD_PROJECT="your-gcp-project-id"
   export GOOGLE_CLOUD_REGION="us-central1"
   chmod +x deploy.sh
   ENABLE_IAP="true" ./deploy.sh
   ```
2. **第 2 步：将允许访问的 Google 邮箱加入 IAP 白名单**
   ```bash
   gcloud iap web add-iam-policy-binding \
     --project="${GOOGLE_CLOUD_PROJECT}" \
     --resource-type=cloud-run \
     --service="music-studio" \
     --region="${GOOGLE_CLOUD_REGION}" \
     --member="user:authorized-user@example.com" \
     --role="roles/iap.httpsResourceAccessor"
   ```
   ✅ **完成！** 白名单内的用户直接打开 Cloud Run 网址并登录 Google 账号即可访问。

---

## 🚑 常见问题快速排查 (Troubleshooting)

### 问题 1：运行 `./deploy.sh` 时提示 `PERMISSION_DENIED` 或 `does not have permission to access projects instance [xxx:setIamPolicy]`
说明您当前登录的账号在该 GCP Project 中只有 `Editor` 权限，缺少绑定 IAM 角色（`resourcemanager.projects.setIamPolicy`）和设置 Cloud Run 鉴权策略（`run.services.setIamPolicy`）的权限。请让该项目的 **GCP 管理员（Owner）** 将 `DEPLOYER_EMAIL` 替换为您的邮箱后执行一次：

```bash
export PROJECT_ID="your-gcp-project-id"           # 替换为目标 Project ID
export DEPLOYER_EMAIL="你的邮箱@example.com"  # 替换为执行部署的工程师邮箱

for ROLE in \
  "roles/run.admin" \
  "roles/iam.serviceAccountUser" \
  "roles/cloudbuild.builds.editor" \
  "roles/artifactregistry.admin" \
  "roles/storage.admin" \
  "roles/serviceusage.serviceUsageAdmin" \
  "roles/iam.serviceAccountAdmin" \
  "roles/resourcemanager.projectIamAdmin"; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="user:${DEPLOYER_EMAIL}" --role="${ROLE}" --condition=None --quiet
done
```

### 问题 2：如果暂时找不到项目 Owner，如何在只有 `Editor` 权限的项目中立即完成部署？
您可以直接复用该项目下已经拥有权限的服务账号（例如 `diarization-studio-sa`），跳过 IAM 绑定并走本地安全代理访问：
```bash
SA_NAME="diarization-studio-sa" SKIP_IAM_SETUP="true" ./deploy.sh
gcloud run services proxy music-studio --project="${GOOGLE_CLOUD_PROJECT}" --region="us-central1" --port=8080
```

### 问题 3：如何让平台读取客户已有的其他 GCS Bucket / Folder 素材？
若客户音视频素材存放在另一个存储桶（如 `gs://customer-media-bucket/music-tracks/`），只需给平台运行时的服务账号授予该存储桶的读取权限：
```bash
gcloud storage buckets add-iam-policy-binding "gs://customer-media-bucket" \
  --member="serviceAccount:music-studio-sa@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com" \
  --role="roles/storage.objectViewer"
```
随后即可在平台网页中直接输入 `gs://customer-media-bucket/music-tracks/` 浏览并加载分析。
