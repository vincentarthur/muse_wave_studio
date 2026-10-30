#!/usr/bin/env bash
# ==============================================================================
# MuseWave Studio — Music Creation & Acoustic Intelligence
# One-Click Cloud Run Deployment Script (Dedicated Service Account + Zero-Plaintext SHA-256 Gatekeeper)
#
# Automatically performs:
#   1. Enable required APIs (Vertex AI, Cloud Run, Cloud Build, Artifact Registry, Cloud Storage)
#   2. Create dedicated least-privilege Service Account (`music-studio-sa@<PROJECT_ID>.iam.gserviceaccount.com`)
#      -> Even when `SKIP_IAM_SETUP="true"` is set, automatically checks and creates the SA if missing!
#   3. Bind least-privilege IAM roles (`aiplatform.user`, `storage.objectUser`, `storage.objectAdmin`, `logging.logWriter`)
#   4. Build container image via Cloud Build (`SKIP_BUILD="true"` supported to reuse built image)
#   5. Deploy to Cloud Run Gen2 with `--service-account="${SA_EMAIL}"` and GCS FUSE volume mount
#
# Supported Authentication Modes:
#   1. Default Zero-Trust IAM Mode:
#        ./deploy_cloud_run.sh
#      Deploys with `--no-allow-unauthenticated`. Access locally via:
#        gcloud run services proxy music-studio --region us-central1 --port 8080
#
#   2. Interactive Zero-Plaintext SHA-256 Passcode Gatekeeper Mode (Recommended):
#        PROMPT_PASSCODE="true" ./deploy_cloud_run.sh
#        PROMPT_PASSCODE="true" SKIP_IAM_SETUP="true" ./deploy_cloud_run.sh
#      Or provide a pre-computed 64-char SHA-256 hex hash directly:
#        MUSIC_STUDIO_PASSCODE_SHA256="<64-hex>" SKIP_IAM_SETUP="true" ./deploy_cloud_run.sh
#      Plaintext password is NEVER stored in shell history or Cloud Run env vars.
#
#   3. Cloud IAP Mode (Google Corp SSO):
#        ENABLE_IAP="true" ./deploy_cloud_run.sh
#
#   4. Reuse Existing Container Image (Fast Re-deploy):
#        SKIP_BUILD="true" SKIP_IAM_SETUP="true" PROMPT_PASSCODE="true" ./deploy_cloud_run.sh
# ==============================================================================
set -euo pipefail

# Allow passing KEY=VALUE or flags as CLI arguments in addition to env vars
for arg in "$@"; do
  case "${arg}" in
    --skip-iam-setup|--skip-iam)
      export SKIP_IAM_SETUP="true"
      ;;
    --skip-build)
      export SKIP_BUILD="true"
      ;;
    SKIP_IAM_SETUP=*)
      export SKIP_IAM_SETUP="${arg#*=}"
      ;;
    SKIP_BUILD=*)
      export SKIP_BUILD="${arg#*=}"
      ;;
    PROMPT_PASSCODE=*)
      export PROMPT_PASSCODE="${arg#*=}"
      ;;
    ENABLE_IAP=*)
      export ENABLE_IAP="${arg#*=}"
      ;;
    PROJECT_ID=*|GOOGLE_CLOUD_PROJECT=*)
      export GOOGLE_CLOUD_PROJECT="${arg#*=}"
      ;;
    REGION=*|CLOUD_RUN_REGION=*|GOOGLE_CLOUD_REGION=*)
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
      export GCS_BUCKET_NAME="${arg#*=}"
      ;;
    GCS_OBJECT_PREFIX=*)
      export GCS_OBJECT_PREFIX="${arg#*=}"
      ;;
    MUSIC_STUDIO_PASSCODE_SHA256=*|PASSCODE_SHA256=*)
      export MUSIC_STUDIO_PASSCODE_SHA256="${arg#*=}"
      ;;
    MUSIC_STUDIO_ACCESS_PASSCODE=*|RAW_PASSCODE=*)
      export MUSIC_STUDIO_ACCESS_PASSCODE="${arg#*=}"
      ;;
  esac
done

DEFAULT_GCLOUD_PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-${DEFAULT_GCLOUD_PROJECT}}}"
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" || "${PROJECT_ID}" == "your-gcp-project-id" ]]; then
  echo "❌ 错误：未检测到有效的 GCP Project ID。请先执行 export GOOGLE_CLOUD_PROJECT=\"your-gcp-project-id\""
  exit 1
fi

REGION="${REGION:-${CLOUD_RUN_REGION:-${GOOGLE_CLOUD_REGION:-us-central1}}}"
SERVICE_NAME="${SERVICE_NAME:-music-studio}"

# Dedicated Service Account (aligned with speaker-diarization-studio architecture)
SA_NAME="${SA_NAME:-music-studio-sa}"
SA_EMAIL="${SA_EMAIL:-${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com}"

AR_REPO="${AR_REPO:-cloud-run-source-deploy}"
IMAGE_URI="${IMAGE_URI:-${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE_NAME}:latest}"

GCS_BUCKET="${GCS_BUCKET:-${GCS_BUCKET_NAME:-${MUSIC_STUDIO_GCS_BUCKET:-${PROJECT_ID}-music-studio-storage}}}"
GCS_PREFIX="${GCS_OBJECT_PREFIX:-music-studio}"

GEMINI_MODEL="${MUSIC_STUDIO_GEMINI_MODEL:-gemini-4-argon}"
if [[ "${GEMINI_MODEL}" == "gemini-4" ]]; then
  GEMINI_MODEL="gemini-4-argon"
fi
THINKING_LEVEL="${MUSIC_STUDIO_THINKING_LEVEL:-MEDIUM}"
LYRIA_MODEL="${MUSIC_STUDIO_LYRIA_MODEL:-lyria-3-pro-preview}"

ENABLE_IAP="${ENABLE_IAP:-false}"
PROMPT_PASSCODE="${PROMPT_PASSCODE:-false}"
SKIP_IAM_SETUP="${SKIP_IAM_SETUP:-false}"
SKIP_BUILD="${SKIP_BUILD:-false}"

ACCESS_PASSCODE="${MUSIC_STUDIO_ACCESS_PASSCODE:-${VIRAL_PITCH_ACCESS_PASSCODE:-}}"
PASSCODE_SHA256="${MUSIC_STUDIO_PASSCODE_SHA256:-${VIRAL_PITCH_PASSCODE_SHA256:-}}"

# 1. Interactive hidden prompt if PROMPT_PASSCODE="true"
if [[ "${PROMPT_PASSCODE}" == "true" && -z "${ACCESS_PASSCODE}" && -z "${PASSCODE_SHA256}" ]]; then
  read -rsp "🔑 请输入要设置的 Music Studio 访问口令 (输入过程完全隐藏，不留存历史记录): " ACCESS_PASSCODE
  echo ""
fi

# 2. Hash plaintext passcode locally into SHA-256 hex digest and wipe plaintext
if [[ -n "${ACCESS_PASSCODE}" && -z "${PASSCODE_SHA256}" ]]; then
  if command -v sha256sum >/dev/null 2>&1; then
    PASSCODE_SHA256="$(printf '%s' "${ACCESS_PASSCODE}" | sha256sum | awk '{print $1}')"
  else
    PASSCODE_SHA256="$(printf '%s' "${ACCESS_PASSCODE}" | shasum -a 256 | awk '{print $1}')"
  fi
  unset ACCESS_PASSCODE
fi

echo "=================================================================="
echo "🎼 Deploying MuseWave Studio (Music Creation & Acoustic Intelligence) to Cloud Run"
echo "  Project ID     : ${PROJECT_ID}"
echo "  Region         : ${REGION}"
echo "  Service Name   : ${SERVICE_NAME}"
echo "  Service Account: ${SA_EMAIL} (Dedicated Least-Privilege SA)"
echo "  Container Image: ${IMAGE_URI}"
echo "  Gemini Engine  : ${GEMINI_MODEL} (thinking_level=${THINKING_LEVEL})"
echo "  Lyria Model    : ${LYRIA_MODEL}"
echo "  GCS Bucket     : gs://${GCS_BUCKET}/${GCS_PREFIX}"
echo "  Skip IAM Setup : ${SKIP_IAM_SETUP}"
echo "  Skip Build     : ${SKIP_BUILD}"
if [[ -n "${PASSCODE_SHA256}" ]]; then
  echo "  Security Mode  : Zero-Plaintext SHA-256 Passcode Gatekeeper"
  echo "  SHA-256 Digest : ${PASSCODE_SHA256:0:12}...${PASSCODE_SHA256: -8}"
elif [[ "${ENABLE_IAP}" == "true" ]]; then
  echo "  Security Mode  : Cloud Identity-Aware Proxy (--iap)"
else
  echo "  Security Mode  : IAM Authenticated (--no-allow-unauthenticated)"
fi
echo "=================================================================="

if [[ "${SKIP_IAM_SETUP,,}" != "true" && "${SKIP_IAM_SETUP}" != "1" ]]; then
  echo "🔧 [1/4] 正在检查并启用必需的 Google Cloud APIs..."
  gcloud services enable \
    aiplatform.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT_ID}" --quiet || true
else
  echo "⏩ [1/4] SKIP_IAM_SETUP=${SKIP_IAM_SETUP} — 跳过 API 启用检查。"
fi

# 即使设置了 SKIP_IAM_SETUP=true，也必须确保专属服务账号存在，否则 Cloud Run 部署会报 NOT_FOUND
SA_JUST_CREATED="false"
if ! gcloud iam service-accounts describe "${SA_EMAIL}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "👤 [2/4] 检测到专属服务账号 (${SA_EMAIL}) 尚未创建，正在自动创建..."
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="MuseWave Music Studio Service Account" \
    --project="${PROJECT_ID}" --quiet
  SA_JUST_CREATED="true"
fi

if [[ "${SKIP_IAM_SETUP,,}" != "true" && "${SKIP_IAM_SETUP}" != "1" ]] || [[ "${SA_JUST_CREATED}" == "true" ]]; then
  echo "👤 [2/4] 正在为专属服务账号 (${SA_EMAIL}) 绑定最小权限 IAM 角色..."
  for ROLE in \
    "roles/aiplatform.user" \
    "roles/serviceusage.serviceUsageConsumer" \
    "roles/logging.logWriter" \
    "roles/storage.objectUser" \
    "roles/storage.objectAdmin"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${SA_EMAIL}" \
      --role="${ROLE}" \
      --condition=None \
      --quiet >/dev/null 2>&1 || true
  done
  if [[ "${SA_JUST_CREATED}" == "true" ]]; then
    echo "⏳ 等待 5 秒以确保新创建的专属 IAM 服务账号在全球端点完成同步..."
    sleep 5
  fi
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

# 确保 GCS Bucket 存在，并为专属服务账号授予 Bucket 读写权限
BUCKET_JUST_CREATED="false"
if [[ -n "${GCS_BUCKET}" ]]; then
  if ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "🪣 检测到 gs://${GCS_BUCKET} 不存在，正在自动创建 (${REGION})..."
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --uniform-bucket-level-access \
      --quiet || true
    BUCKET_JUST_CREATED="true"
  fi
  if [[ "${SKIP_IAM_SETUP,,}" != "true" && "${SKIP_IAM_SETUP}" != "1" ]] || [[ "${SA_JUST_CREATED}" == "true" ]] || [[ "${BUCKET_JUST_CREATED}" == "true" ]]; then
    echo "🪣 正在确保专属服务账号 (${SA_EMAIL}) 拥有 gs://${GCS_BUCKET} 的读写权限..."
    for B_ROLE in "roles/storage.objectUser" "roles/storage.objectAdmin"; do
      gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="${B_ROLE}" \
        --quiet >/dev/null 2>&1 || true
    done
  fi
fi

# 分离式构建 + 部署：显式使用 gcloud builds submit 构建镜像并推送到 Artifact Registry，
# 规避 `gcloud run deploy --source .` 内部源码构建轮询器 (deployer.py) 的 404 NOT_FOUND 缺陷
if [[ "${SKIP_BUILD,,}" != "true" && "${SKIP_BUILD}" != "1" ]]; then
  echo "🏗️ [3/4] 正在通过 Cloud Build 构建容器镜像 (${IMAGE_URI})..."
  if ! gcloud builds submit --tag "${IMAGE_URI}" --project "${PROJECT_ID}" .; then
    echo "⚠️ 全局端点构建返回异常，正在尝试区域端点 (${REGION}) 构建..."
    gcloud builds submit --tag "${IMAGE_URI}" --project "${PROJECT_ID}" --region "${REGION}" .
  fi
else
  echo "⏭️ [3/4] 已设置 SKIP_BUILD=true，直接复用镜像: ${IMAGE_URI}"
fi

# 部署到 Cloud Run (显式绑定专属服务账号 --service-account="${SA_EMAIL}")
echo "🚢 [4/4] 正在将容器镜像部署到 Cloud Run 服务 (${SERVICE_NAME}，服务账号: ${SA_EMAIL})..."

ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GCS_BUCKET_NAME=${GCS_BUCKET},GCS_OBJECT_PREFIX=${GCS_PREFIX},MUSIC_STUDIO_SERVICE_ACCOUNT=${SA_EMAIL},MUSIC_STUDIO_GEMINI_MODEL=${GEMINI_MODEL},MUSIC_STUDIO_THINKING_LEVEL=${THINKING_LEVEL},MUSIC_STUDIO_LYRIA_MODEL=${LYRIA_MODEL},MUSIC_STUDIO_WORKSPACE_DIR=/mnt/gcs/music-studio-workspace,CLOUD_LOGGING_JSON=true,PYTHONUNBUFFERED=1"

DEPLOY_ARGS=(
  beta run deploy "${SERVICE_NAME}"
  --image "${IMAGE_URI}"
  --project "${PROJECT_ID}"
  --region "${REGION}"
  --service-account "${SA_EMAIL}"
  --execution-environment gen2
  --no-cpu-throttling
  --session-affinity
  --cpu 4
  --memory 8Gi
  --timeout 3600
  --concurrency 20
  --min-instances 0
  --max-instances "${MAX_INSTANCES:-4}"
)

if [[ -n "${PASSCODE_SHA256}" ]]; then
  echo "🔐 启用【方案二：零明文 SHA-256 登录门禁模式】— 云端仅存储 SHA-256 哈希 (${PASSCODE_SHA256:0:12}...)"
  DEPLOY_ARGS+=(--allow-unauthenticated)
  ENV_VARS="${ENV_VARS},MUSIC_STUDIO_PASSCODE_SHA256=${PASSCODE_SHA256}"
elif [[ "${ENABLE_IAP}" == "true" ]]; then
  echo "🛡️ 启用【方案三：Cloud Run 原生 IAP (Identity-Aware Proxy)】"
  DEPLOY_ARGS+=(--no-allow-unauthenticated --iap)
else
  echo "🔒 启用【方案一：私密 IAM 零信任边界 (--no-allow-unauthenticated)】"
  DEPLOY_ARGS+=(--no-allow-unauthenticated)
fi

if [[ -n "${GCS_BUCKET}" ]]; then
  echo "☁️ 启用 Cloud Run 原生 GCS FUSE 持久化挂载: gs://${GCS_BUCKET}"
  DEPLOY_ARGS+=(
    --add-volume "name=studio-gcs-vol,type=cloud-storage,bucket=${GCS_BUCKET}"
    --add-volume-mount "volume=studio-gcs-vol,mount-path=/mnt/gcs"
  )
fi

DEPLOY_ARGS+=(--set-env-vars "${ENV_VARS}")

gcloud "${DEPLOY_ARGS[@]}"

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)' 2>/dev/null || echo "https://${SERVICE_NAME}-xxxx.${REGION}.run.app")"

echo ""
echo "=================================================================="
echo "✅ MuseWave Studio 部署完成！"
echo "🌐 Service URL     : ${SERVICE_URL}"
echo "👤 Service Account : ${SA_EMAIL}"
echo "☁️ GCS Storage     : gs://${GCS_BUCKET}/${GCS_PREFIX}"
if [[ -n "${PASSCODE_SHA256}" ]]; then
  echo "🔒 Login Page      : ${SERVICE_URL}"
  echo "🔑 Direct Token URL: ${SERVICE_URL}/?token=${PASSCODE_SHA256}"
elif [[ "${ENABLE_IAP}" == "true" ]]; then
  echo "🛡️ IAP Access URL  : ${SERVICE_URL}"
else
  echo "🔐 Local Proxy Cmd : gcloud run services proxy ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --port=8080"
fi
echo "=================================================================="
