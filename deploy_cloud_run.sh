#!/usr/bin/env bash
# ==============================================================================
# 一键安全极速部署 MuseWave Studio (Gemini 4 x Lyria 3 全链路音乐工作台) 到 Google Cloud Run
# 自动完成：启用必需 API -> 创建最小权限服务账号 -> 绑定 IAM 权限 -> Cloud Build 构建镜像 -> 部署 Cloud Run
# 支持三种安全访问模式：
#   1. 默认模式 (IAM 零信任私密模式):
#      ./deploy.sh
#   2. 零明文 SHA-256 口令门禁模式 (本地交互式隐藏输入口令，云端仅存 64 位 SHA-256 哈希，客户免装 gcloud):
#      PROMPT_PASSCODE="true" ./deploy.sh
#      (或直接传入哈希: MUSIC_STUDIO_PASSCODE_SHA256="<64位sha256>" ./deploy.sh)
#   3. 企业级原生 IAP 模式 (Google 账号白名单):
#      ENABLE_IAP="true" ./deploy.sh
#   4. 复用已构建镜像直接极速部署 (若 Cloud Build 已完成镜像构建):
#      SKIP_BUILD="true" ./deploy.sh
# ==============================================================================
set -euo pipefail

# 支持将 KEY=VALUE 作为命令行参数直接传入 (例如: ./deploy.sh PROMPT_PASSCODE=true SKIP_IAM_SETUP=true)
for arg in "$@"; do
  case "${arg}" in
    PROJECT_ID=*|GOOGLE_CLOUD_PROJECT=*)
      export GOOGLE_CLOUD_PROJECT="${arg#*=}"
      ;;
    REGION=*|GOOGLE_CLOUD_REGION=*)
      export GOOGLE_CLOUD_REGION="${arg#*=}"
      ;;
    SERVICE_NAME=*)
      export SERVICE_NAME="${arg#*=}"
      ;;
    SA_NAME=*)
      export SA_NAME="${arg#*=}"
      ;;
    SA_EMAIL=*)
      export SA_EMAIL="${arg#*=}"
      ;;
    GCS_BUCKET=*|GCS_BUCKET_NAME=*|MUSIC_STUDIO_GCS_BUCKET=*)
      export GCS_BUCKET="${arg#*=}"
      ;;
    GCS_OBJECT_PREFIX=*)
      export GCS_OBJECT_PREFIX="${arg#*=}"
      ;;
    MUSIC_STUDIO_GEMINI_MODEL=*|GEMINI_MODEL=*)
      export MUSIC_STUDIO_GEMINI_MODEL="${arg#*=}"
      ;;
    MUSIC_STUDIO_LYRIA_MODEL=*|LYRIA_MODEL=*)
      export MUSIC_STUDIO_LYRIA_MODEL="${arg#*=}"
      ;;
    PROMPT_PASSCODE=*)
      export PROMPT_PASSCODE="${arg#*=}"
      ;;
    MUSIC_STUDIO_PASSCODE_SHA256=*|PASSCODE_SHA256=*)
      export MUSIC_STUDIO_PASSCODE_SHA256="${arg#*=}"
      ;;
    MUSIC_STUDIO_ACCESS_PASSCODE=*|RAW_PASSCODE=*)
      export MUSIC_STUDIO_ACCESS_PASSCODE="${arg#*=}"
      ;;
    ENABLE_IAP=*)
      export ENABLE_IAP="${arg#*=}"
      ;;
    SKIP_IAM_SETUP=*)
      export SKIP_IAM_SETUP="${arg#*=}"
      ;;
    SKIP_BUILD=*)
      export SKIP_BUILD="${arg#*=}"
      ;;
  esac
done

DEFAULT_GCLOUD_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${DEFAULT_GCLOUD_PROJECT}}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "❌ 错误：未检测到 GCP Project ID。请先执行 export GOOGLE_CLOUD_PROJECT=\"your-gcp-project-id\" 或使用 --project <PROJECT_ID>"
  exit 1
fi
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-music-studio}"
SA_NAME="${SA_NAME:-music-studio-sa}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"
AR_REPO="${AR_REPO:-cloud-run-source-deploy}"
IMAGE_URI="${IMAGE_URI:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:latest}"

# 可选：若将素材与持久化数据放在 GCS Bucket，填写 Bucket 名称
GCS_BUCKET="${GCS_BUCKET:-${GCS_BUCKET_NAME:-${MUSIC_STUDIO_GCS_BUCKET:-}}}"
GCS_OBJECT_PREFIX="${GCS_OBJECT_PREFIX:-music-studio}"

# 可选：零明文 SHA-256 门禁模式（支持交互式隐藏输入 PROMPT_PASSCODE=true 或直接传 SHA-256 哈希）
PROMPT_PASSCODE="${PROMPT_PASSCODE:-false}"
PASSCODE_SHA256="${MUSIC_STUDIO_PASSCODE_SHA256:-}"
RAW_PASSCODE="${MUSIC_STUDIO_ACCESS_PASSCODE:-}"

# 可选：若希望开启 Cloud Run 原生 IAP (Identity-Aware Proxy)
ENABLE_IAP="${ENABLE_IAP:-false}"

# 可选：是否跳过启用 API 与重复绑定 IAM (默认 false；注意若服务账号不存在仍会自动创建以避免 NOT_FOUND)
SKIP_IAM_SETUP="${SKIP_IAM_SETUP:-false}"

# 可选：是否跳过 Cloud Build 镜像构建，直接复用 Artifact Registry 中已有的镜像部署
SKIP_BUILD="${SKIP_BUILD:-false}"

if [[ "${PROMPT_PASSCODE}" == "true" && -z "${PASSCODE_SHA256}" && -z "${RAW_PASSCODE}" ]]; then
  read -rsp "🔑 请输入要设置的访问口令 (输入过程完全隐藏，不留存历史记录): " RAW_PASSCODE
  echo ""
fi

if [[ -n "${RAW_PASSCODE}" && -z "${PASSCODE_SHA256}" ]]; then
  # 在本地直接计算不可逆的 SHA-256 摘要（兼容 Linux sha256sum 与 macOS shasum -a 256）
  if command -v sha256sum >/dev/null 2>&1; then
    PASSCODE_SHA256=$(printf "%s" "${RAW_PASSCODE}" | sha256sum | awk '{print $1}')
  else
    PASSCODE_SHA256=$(printf "%s" "${RAW_PASSCODE}" | shasum -a 256 | awk '{print $1}')
  fi
  unset RAW_PASSCODE
fi

echo "🚀 部署项目: ${PROJECT_ID} | 区域: ${REGION} | 服务名: ${SERVICE_NAME} | 服务账号: ${SA_EMAIL} | GCS Bucket: ${GCS_BUCKET:-<未指定>}"

if [[ "${SKIP_IAM_SETUP}" != "true" ]]; then
  echo "🔧 [1/4] 正在检查并启用必需的 Google Cloud APIs..."
  gcloud services enable \
    aiplatform.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT_ID}" --quiet || true
fi

# 即使设置了 SKIP_IAM_SETUP=true，也必须确保服务账号存在，否则 Cloud Run 部署会报 NOT_FOUND: Requested entity was not found
SA_JUST_CREATED="false"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "👤 [2/4] 检测到服务账号 (${SA_EMAIL}) 尚未创建，正在自动创建..."
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Music Studio Service Account" \
    --project="${PROJECT_ID}" --quiet
  SA_JUST_CREATED="true"
  echo "⏳ 等待 8 秒以确保新创建的 IAM 服务账号在全球端点完成同步（避免立即绑定角色时报 does not exist）..."
  sleep 8
fi

if [[ "${SKIP_IAM_SETUP}" != "true" || "${SA_JUST_CREATED}" == "true" ]]; then
  echo "👤 [2/4] 正在为服务账号 (${SA_EMAIL}) 绑定必需 IAM 权限..."
  for ROLE in \
    "roles/aiplatform.user" \
    "roles/serviceusage.serviceUsageConsumer" \
    "roles/logging.logWriter" \
    "roles/storage.objectUser"; do
    echo "   ├─ 正在绑定 ${ROLE} ..."
    if ! gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="${ROLE}" \
      --condition=None \
      --quiet >/dev/null; then
      echo "   ⚠️ 绑定 ${ROLE} 失败（若刚创建 SA 可能是同步延迟，正在重试一次...）"
      sleep 5
      gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${ROLE}" \
        --condition=None \
        --quiet >/dev/null || echo "   ❌ 绑定 ${ROLE} 仍失败：请检查当前账号是否拥有该项目的 Project IAM Admin (resourcemanager.projects.setIamPolicy) 或 Owner 权限！"
    fi
  done
fi

# 确保 Artifact Registry 镜像仓库存在，避免构建或推送时报 NOT_FOUND
if ! gcloud artifacts repositories describe "${AR_REPO}" --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "📦 正在创建 Artifact Registry 容器仓库 (${AR_REPO} in ${REGION})..."
  gcloud artifacts repositories create "${AR_REPO}" \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Cloud Run container images" \
    --project="${PROJECT_ID}" --quiet || true
fi

if [[ -n "${GCS_BUCKET}" ]]; then
  # 确保 GCS Bucket 存在，避免 Cloud Run GCSFuse 挂载时报 NOT_FOUND
  if ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "🪣 检测到 gs://${GCS_BUCKET} 不存在，正在自动创建 (${REGION})..."
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --uniform-bucket-level-access --quiet || true
  fi
  echo "🪣 正在确保服务账号 (${SA_EMAIL}) 拥有 gs://${GCS_BUCKET} 的读写权限 (roles/storage.objectUser)..."
  gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/storage.objectUser" \
    --quiet >/dev/null 2>&1 || true
fi

# 分离式构建 + 部署：显式使用 gcloud builds submit 构建镜像并推送到 Artifact Registry，
# 彻底规避 `gcloud run deploy --source .` 内部源码构建轮询器 (deployer.py) 的 404 NOT_FOUND 缺陷
if [[ "${SKIP_BUILD}" != "true" ]]; then
  echo "🏗️ [3/4] 正在通过 Cloud Build 构建容器镜像 (${IMAGE_URI})..."
  if ! gcloud builds submit --tag "${IMAGE_URI}" --project "${PROJECT_ID}" .; then
    echo "⚠️ 全局端点构建返回异常，正在尝试区域端点 (${REGION}) 构建..."
    gcloud builds submit --tag "${IMAGE_URI}" --project "${PROJECT_ID}" --region "${REGION}" .
  fi
else
  echo "⏭️ [3/4] 已设置 SKIP_BUILD=true，直接复用镜像: ${IMAGE_URI}"
fi

echo "🚢 [4/4] 正在将容器镜像部署到 Cloud Run 服务 (${SERVICE_NAME})..."

# 统一拼接所有环境变量到单个 --set-env-vars 中（gcloud 不允许同时传入 --set-env-vars 和 --update-env-vars）
# Vertex AI 上的 Gemini 4 Publisher Model ID 为 gemini-4-argon (thinking_level=MEDIUM)
GEMINI_MODEL="${MUSIC_STUDIO_GEMINI_MODEL:-gemini-4-argon}"
if [[ "${GEMINI_MODEL}" == "gemini-4" ]]; then
  GEMINI_MODEL="gemini-4-argon"
fi
LYRIA_MODEL="${MUSIC_STUDIO_LYRIA_MODEL:-lyria-3-pro-preview}"
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,MUSIC_STUDIO_SERVICE_ACCOUNT=${SA_EMAIL},MUSIC_STUDIO_GEMINI_MODEL=${GEMINI_MODEL},MUSIC_STUDIO_THINKING_LEVEL=MEDIUM,MUSIC_STUDIO_LYRIA_MODEL=${LYRIA_MODEL},GCS_OBJECT_PREFIX=${GCS_OBJECT_PREFIX}"

DEPLOY_ARGS=(
  run deploy "${SERVICE_NAME}"
  --image "${IMAGE_URI}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --service-account "${SA_EMAIL}"
  --execution-environment gen2
  --cpu 4
  --memory 8Gi
  --no-cpu-throttling
  --timeout 1800
  --concurrency 20
  --min-instances 0
  --max-instances 2
)

if [[ -n "${PASSCODE_SHA256}" ]]; then
  echo "🔐 启用【方案二：零明文 SHA-256 登录门禁模式】— 云端仅存储 SHA-256 哈希 (${PASSCODE_SHA256:0:12}...)"
  DEPLOY_ARGS+=(--allow-unauthenticated)
  ENV_VARS="${ENV_VARS},MUSIC_STUDIO_PASSCODE_SHA256=${PASSCODE_SHA256}"
else
  echo "🔒 启用【私密 IAM 零信任边界 (--no-allow-unauthenticated)】"
  DEPLOY_ARGS+=(--no-allow-unauthenticated)
fi

if [[ -n "${GCS_BUCKET}" ]]; then
  echo "☁️ 启用 Cloud Run 原生 GCS FUSE 持久化挂载: gs://${GCS_BUCKET}"
  DEPLOY_ARGS+=(
    --add-volume "name=music-data,type=cloud-storage,bucket=${GCS_BUCKET}"
    --add-volume-mount "volume=music-data,mount-path=/app/workspace/runs"
  )
  ENV_VARS="${ENV_VARS},GCS_BUCKET_NAME=${GCS_BUCKET},MUSIC_STUDIO_GCS_BUCKET=${GCS_BUCKET}"
fi

DEPLOY_ARGS+=(--set-env-vars "${ENV_VARS}")

gcloud "${DEPLOY_ARGS[@]}"

if [[ "${ENABLE_IAP}" == "true" ]]; then
  echo "🛡️ 正在开启【方案三：Cloud Run 原生 IAP (Identity-Aware Proxy)】..."
  gcloud beta run services update "${SERVICE_NAME}" \
    --project="${PROJECT_ID}" \
    --region="${REGION}" \
    --no-allow-unauthenticated \
    --iap
fi

SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format="value(status.url)" 2>/dev/null || echo "https://${SERVICE_NAME}-xxxx.${REGION}.run.app")

echo ""
echo "=============================================================================="
echo "✅ 部署完成！服务地址: ${SERVICE_URL}"
if [[ -n "${PASSCODE_SHA256}" ]]; then
  echo "👉 【方案二：零明文 SHA-256 门禁已开启】"
  echo "   1. 客户直接打开干净网址（进入掩码密码登录页，前端 SHA-256 哈希后 POST 验证，URL 零明文）："
  echo "      ${SERVICE_URL}"
  echo "   2. 或发送带 SHA-256 Token 的免输跳转链接（打开后自动种下 HttpOnly Cookie 并 303 抹除地址栏参数）："
  echo "      ${SERVICE_URL}/?token=${PASSCODE_SHA256}"
elif [[ "${ENABLE_IAP}" == "true" ]]; then
  echo "👉 【方案三：IAP Google 账号白名单访问】请确认已在 IAP 中授权客户邮箱后直接打开："
  echo "   ${SERVICE_URL}"
else
  echo "👉 【方案一：IAM 本地安全隧道访问】请在终端运行以下命令："
  echo "   gcloud run services proxy ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --port=8080"
  echo "   然后浏览器打开: http://127.0.0.1:8080"
fi
echo "=============================================================================="
