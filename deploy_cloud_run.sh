#!/usr/bin/env bash
# ==============================================================================
# MuseWave Studio — Music Creation & Acoustic Intelligence
# One-Click Cloud Run Deployment Script (Zero-Plaintext SHA-256 Gatekeeper)
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
# Optional Parameters:
#   - SKIP_IAM_SETUP="true" (or CLI flag `--skip-iam-setup`):
#       Skip enabling APIs, creating service accounts, and re-running project/bucket
#       IAM policy bindings on subsequent deployments.
# ==============================================================================
set -euo pipefail

# Allow passing KEY=VALUE or --skip-iam-setup as CLI arguments in addition to env vars
for arg in "$@"; do
  case "${arg}" in
    --skip-iam-setup|--skip-iam)
      SKIP_IAM_SETUP="true"
      ;;
    SKIP_IAM_SETUP=*)
      SKIP_IAM_SETUP="${arg#*=}"
      ;;
    PROMPT_PASSCODE=*)
      PROMPT_PASSCODE="${arg#*=}"
      ;;
    ENABLE_IAP=*)
      ENABLE_IAP="${arg#*=}"
      ;;
    PROJECT_ID=*|GOOGLE_CLOUD_PROJECT=*)
      GOOGLE_CLOUD_PROJECT="${arg#*=}"
      ;;
    GCS_BUCKET=*|GCS_BUCKET_NAME=*)
      GCS_BUCKET_NAME="${arg#*=}"
      ;;
  esac
done

PROJECT_ID="${PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo 'your-gcp-project-id')}}"
REGION="${REGION:-${CLOUD_RUN_REGION:-${GOOGLE_CLOUD_REGION:-us-central1}}}"
SERVICE_NAME="${SERVICE_NAME:-music-studio}"
GCS_BUCKET="${GCS_BUCKET:-${GCS_BUCKET_NAME:-${MUSIC_STUDIO_GCS_BUCKET:-${PROJECT_ID}-music-studio-storage}}}"
GCS_PREFIX="${GCS_OBJECT_PREFIX:-music-studio}"

GEMINI_MODEL="${MUSIC_STUDIO_GEMINI_MODEL:-gemini-4-argon}"
THINKING_LEVEL="${MUSIC_STUDIO_THINKING_LEVEL:-MEDIUM}"
LYRIA_MODEL="${MUSIC_STUDIO_LYRIA_MODEL:-lyria-3-pro-preview}"

ENABLE_IAP="${ENABLE_IAP:-false}"
PROMPT_PASSCODE="${PROMPT_PASSCODE:-false}"
SKIP_IAM_SETUP="${SKIP_IAM_SETUP:-false}"
SA_EMAIL="${SA_EMAIL:-}"

ACCESS_PASSCODE="${MUSIC_STUDIO_ACCESS_PASSCODE:-${VIRAL_PITCH_ACCESS_PASSCODE:-}}"
PASSCODE_SHA256="${MUSIC_STUDIO_PASSCODE_SHA256:-${VIRAL_PITCH_PASSCODE_SHA256:-}}"

# 1. Interactive hidden prompt if PROMPT_PASSCODE="true"
if [[ "${PROMPT_PASSCODE}" == "true" && -z "${ACCESS_PASSCODE}" && -z "${PASSCODE_SHA256}" ]]; then
  read -rsp "🔑 Enter desired Access Passcode for Music Studio (input hidden): " ACCESS_PASSCODE
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
echo "  Gemini Engine  : ${GEMINI_MODEL} (thinking_level=${THINKING_LEVEL})"
echo "  Lyria Model    : ${LYRIA_MODEL}"
echo "  GCS Bucket     : gs://${GCS_BUCKET}/${GCS_PREFIX}"
echo "  Skip IAM Setup : ${SKIP_IAM_SETUP}"
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
  # 3. Enable required Google Cloud APIs
  echo "[1/4] Enabling Cloud Run, Cloud Build, Artifact Registry, Vertex AI & Storage APIs..."
  gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    aiplatform.googleapis.com \
    storage.googleapis.com \
    --project="${PROJECT_ID}" --quiet || true

  # 4. Ensure GCS Bucket exists for uploaded audio, generated tracks & reports
  echo "[2/4] Ensuring GCS bucket gs://${GCS_BUCKET} exists..."
  if ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --uniform-bucket-level-access \
      --quiet || true
    echo "  Created bucket gs://${GCS_BUCKET}"
  else
    echo "  Bucket gs://${GCS_BUCKET} already exists."
  fi

  # 5. Grant Vertex AI User & Storage Object Admin to runtime Service Account
  echo "[3/4] Configuring IAM roles for Cloud Run runtime service account..."
  if [[ -z "${SA_EMAIL}" ]]; then
    PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
    TARGET_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  else
    TARGET_SA="${SA_EMAIL}"
  fi

  for ROLE in \
    "roles/aiplatform.user" \
    "roles/serviceusage.serviceUsageConsumer" \
    "roles/logging.logWriter" \
    "roles/storage.objectAdmin"; do
    gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
      --member="serviceAccount:${TARGET_SA}" \
      --role="${ROLE}" \
      --condition=None \
      --quiet >/dev/null 2>&1 || true
  done

  gcloud storage buckets add-iam-policy-binding "gs://${GCS_BUCKET}" \
    --member="serviceAccount:${TARGET_SA}" \
    --role="roles/storage.objectAdmin" \
    --quiet >/dev/null 2>&1 || true
else
  echo "⏩ [1-3/4] SKIP_IAM_SETUP=${SKIP_IAM_SETUP} — Skipping API enablement & IAM policy bindings."
  if [[ -n "${GCS_BUCKET}" ]] && ! gcloud storage buckets describe "gs://${GCS_BUCKET}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    echo "📦 Creating missing GCS bucket gs://${GCS_BUCKET}..."
    gcloud storage buckets create "gs://${GCS_BUCKET}" \
      --project="${PROJECT_ID}" \
      --location="${REGION}" \
      --uniform-bucket-level-access \
      --quiet || true
  fi
fi

# 6. Build and Deploy to Cloud Run (Gen2 execution environment + GCS FUSE mount)
echo "[4/4] Building container and deploying ${SERVICE_NAME} to Cloud Run..."

AUTH_FLAGS=("--no-allow-unauthenticated")
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=global,GCS_BUCKET_NAME=${GCS_BUCKET},GCS_OBJECT_PREFIX=${GCS_PREFIX},MUSIC_STUDIO_GEMINI_MODEL=${GEMINI_MODEL},MUSIC_STUDIO_THINKING_LEVEL=${THINKING_LEVEL},MUSIC_STUDIO_LYRIA_MODEL=${LYRIA_MODEL},MUSIC_STUDIO_WORKSPACE_DIR=/mnt/gcs/music-studio-workspace,CLOUD_LOGGING_JSON=true,PYTHONUNBUFFERED=1"

if [[ -n "${PASSCODE_SHA256}" ]]; then
  AUTH_FLAGS=("--allow-unauthenticated")
  ENV_VARS="${ENV_VARS},MUSIC_STUDIO_PASSCODE_SHA256=${PASSCODE_SHA256}"
elif [[ "${ENABLE_IAP}" == "true" ]]; then
  AUTH_FLAGS=("--no-allow-unauthenticated" "--iap")
fi

SA_FLAGS=()
if [[ -n "${SA_EMAIL}" ]]; then
  SA_FLAGS=("--service-account=${SA_EMAIL}")
fi

gcloud beta run deploy "${SERVICE_NAME}" \
  --source="." \
  --project="${PROJECT_ID}" \
  --region="${REGION}" \
  --execution-environment=gen2 \
  --no-cpu-throttling \
  --session-affinity \
  --cpu=4 \
  --memory=8Gi \
  --timeout=3600 \
  --concurrency=20 \
  --min-instances=0 \
  --max-instances="${MAX_INSTANCES:-4}" \
  "${AUTH_FLAGS[@]}" \
  "${SA_FLAGS[@]}" \
  --set-env-vars="${ENV_VARS}" \
  --add-volume="name=studio-gcs-vol,type=cloud-storage,bucket=${GCS_BUCKET}" \
  --add-volume-mount="volume=studio-gcs-vol,mount-path=/mnt/gcs"

SERVICE_URL="$(gcloud run services describe "${SERVICE_NAME}" --project="${PROJECT_ID}" --region="${REGION}" --format='value(status.url)')"

echo ""
echo "=================================================================="
echo "✅ Music Studio Successfully Deployed!"
echo "🌐 Service URL : ${SERVICE_URL}"
echo "☁️ GCS Storage : gs://${GCS_BUCKET}/${GCS_PREFIX}"
if [[ -n "${PASSCODE_SHA256}" ]]; then
  echo "🔒 Login Page  : ${SERVICE_URL}"
  echo "🔑 Direct Token: ${SERVICE_URL}/?token=${PASSCODE_SHA256}"
else
  echo "🔐 Local Proxy : gcloud run services proxy ${SERVICE_NAME} --project=${PROJECT_ID} --region=${REGION} --port=8080"
fi
echo "=================================================================="
