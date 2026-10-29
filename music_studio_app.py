#!/usr/bin/env python3
"""Universal Configurable Music Studio — Backend Server & Engine.

Capabilities:
1. Music Generation (Single Prompt, Batch Generation, Custom Prompt List Upload)
   - Powered by Gemini (`gemini-4-argon`, thinking_level="MEDIUM") + Lyria 3 Pro / Clip
   - Implements Lyria 3 multi-section arrangement & duration-alignment best practices:
     * Multi-Section SongPlan & self-contained English per-section arrangement descriptions
     * Structured `[Intro]`, `[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]` section planning, BPM, and exact target duration
     * Target-language lyrics lock (is_user_provided=True for Group A; Gemini
     lyric composition for Group B;
       strict instrumental lock for Group C)
     * Artist-name & copyright-safe acoustic style sanitizer
     * Dual AUDIO+TEXT modality capture + tail RMS cosine fade-out mastering guard
2. Deep Music Analysis (Powered by gemini-4-argon, thinking_level="MEDIUM")
   - 16-field global acoustic/musical summary, BPM/Key, section-by-section
   timeline & energy curve (1-10),
     instrumentation breakdown, timed lyrics transcription, Mix/Mastering
     diagnostics, and Lyria 3 SongPlan reconstruction.
3. Multi-Track Music Evaluation & Comparison Arena (Powered by gemini-4-argon,
thinking_level="MEDIUM")
   - Simultaneous multimodal comparative listening across 2-6 tracks (Blind or
   Open mode)
   - 6-Dimension rubric scoring (0-10 per dimension, weighted 0-100 total),
   pairwise win/tie/loss matrix,
     and actionable Lyria prompt engineering feedback.
4. Functional Tabs with Dynamic GCS Auto-Discovery & Dataset Loading:
   - Group A: User-Lyrics Vocal Song Studio
   - Group B: Prompt-to-Lyrics Vocal Song Studio
   - Group C: Pure Instrumental & BGM Studio
   - Benchmark & Batch Factory (Auto-discovers and loads datasets & audio directly from GCS Bucket)
5. Configurable GCS Bucket Storage (`GCSStorageManager`)
   - Dynamically discovers, streams, and archives uploaded audio, prompt lists, generated music tracks, and
   analysis/evaluation JSONs.
6. Cloud Run Zero-Plaintext SHA-256 Passcode Gatekeeper.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import contextvars
import csv
import dataclasses
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from typing import Any
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile

import fastapi
from fastapi import Body, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
  from google import genai
  from google.genai import types

  HAS_GENAI_SDK = True
except ImportError:
  HAS_GENAI_SDK = False

try:
  import google.auth
  import google.auth.transport.requests

  HAS_GOOGLE_AUTH = True
except ImportError:
  HAS_GOOGLE_AUTH = False

try:
  from google.cloud import storage as gcs_storage

  HAS_GCS_SDK = True
except ImportError:
  HAS_GCS_SDK = False


# ==============================================================================
# 0. Paths & Runtime Directories
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATASET_JSON_PATH = DATA_DIR / "text2music_120_dataset.json"

WORKSPACE_DIR = Path(
    os.environ.get("MUSIC_STUDIO_WORKSPACE_DIR", str(BASE_DIR / "workspace"))
)
UPLOADS_AUDIO_DIR = WORKSPACE_DIR / "uploads" / "audio"
UPLOADS_LISTS_DIR = WORKSPACE_DIR / "uploads" / "prompt_lists"
GENERATED_DIR = WORKSPACE_DIR / "generations"
ANALYSES_DIR = WORKSPACE_DIR / "analyses"
EVALUATIONS_DIR = WORKSPACE_DIR / "evaluations"
COST_REPORTS_DIR = WORKSPACE_DIR / "cost_reports"
JOBS_DIR = WORKSPACE_DIR / "jobs"
GCS_CACHE_DIR = WORKSPACE_DIR / "gcs_cache"
CONFIG_FILE_PATH = WORKSPACE_DIR / "platform_config.json"

for _d in [
    DATA_DIR,
    WORKSPACE_DIR,
    UPLOADS_AUDIO_DIR,
    UPLOADS_LISTS_DIR,
    GENERATED_DIR,
    ANALYSES_DIR,
    EVALUATIONS_DIR,
    COST_REPORTS_DIR,
    JOBS_DIR,
    GCS_CACHE_DIR,
]:
  _d.mkdir(parents=True, exist_ok=True)


# ==============================================================================
# 0.5 Cloud Run Structured JSON Logger & Brand Icon SVG
# ==============================================================================
STUDIO_APP_NAME = "MuseWave Studio · 全景音乐创作与声学洞察"
STUDIO_APP_NAME_EN = "MuseWave Studio — Music Creation & Acoustic Intelligence"

STUDIO_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <defs>
    <linearGradient id="mwBg" x1="0" y1="0" x2="64" y2="64" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#6366f1"/>
      <stop offset="52%" stop-color="#8b5cf6"/>
      <stop offset="100%" stop-color="#00d4ff"/>
    </linearGradient>
  </defs>
  <rect x="2" y="2" width="60" height="60" rx="16" fill="url(#mwBg)"/>
  <rect x="3" y="3" width="58" height="58" rx="15" stroke="#ffffff" stroke-opacity="0.28" stroke-width="1.5"/>
  <rect x="9" y="29" width="4.5" height="14" rx="2.25" fill="#ffffff" fill-opacity="0.76"/>
  <rect x="16.5" y="21" width="4.5" height="28" rx="2.25" fill="#ffffff" fill-opacity="0.92"/>
  <rect x="24" y="31" width="4.5" height="13" rx="2.25" fill="#ffffff" fill-opacity="0.72"/>
  <path d="M35 43V18.2C35 16.75 35.95 15.48 37.34 15.05L52.84 10.25C54.62 9.7 56.4 11.03 56.4 12.89V37.6" stroke="#ffffff" stroke-width="4.4" stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M35 23.6L56.4 17.0" stroke="#ffffff" stroke-width="3.6" stroke-linecap="round" stroke-opacity="0.88"/>
  <circle cx="30.4" cy="43.8" r="5.6" fill="#ffffff"/>
  <circle cx="51.8" cy="38.4" r="5.6" fill="#ffffff"/>
  <path d="M18.5 8.5L19.75 12.15L23.4 13.4L19.75 14.65L18.5 18.3L17.25 14.65L13.6 13.4L17.25 12.15L18.5 8.5Z" fill="#a5f3fc"/>
</svg>"""

_LOG_LOCK = threading.Lock()
_RUNTIME_LOGS: list[dict[str, Any]] = []
_MAX_RUNTIME_LOGS = 1200
RUNTIME_LOG_FILE = Path(tempfile.gettempdir()) / "music_studio_runtime_debug.log"
_TRACE_CONTEXT_VAR: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "music_studio_trace_context", default={}
)


def log_runtime(
    level: str,
    stage: str,
    message: str,
    job_id: str = "",
    item_id: str = "",
    extra: dict[str, Any] | None = None,
    exc: BaseException | None = None,
) -> dict[str, Any]:
  """Emits structured JSON logs for Google Cloud Run Cloud Logging (`stdout`/`stderr`), `/tmp` log file, and in-memory `/api/logs` ring buffer."""
  now_dt = datetime.now(timezone.utc)
  now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
  lvl = (level or "INFO").upper()
  severity = (
      lvl
      if lvl in ("DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY")
      else "INFO"
  )

  tb_str = ""
  if exc is not None:
    tb_str = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()

  extra_dict: dict[str, Any] = dict(extra or {})
  if job_id and "job_id" not in extra_dict:
    extra_dict["job_id"] = job_id
  if item_id and "item_id" not in extra_dict:
    extra_dict["item_id"] = item_id

  extra_str = ""
  if extra_dict:
    try:
      extra_str = " | " + json.dumps(extra_dict, ensure_ascii=False, default=str)
    except Exception:
      extra_str = f" | {extra_dict}"

  formatted_msg = f"[{stage}] {message}{extra_str}"
  is_err_sev = severity in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY")
  if tb_str and is_err_sev:
    formatted_msg = f"{formatted_msg}\n{tb_str}"

  trace_ctx = _TRACE_CONTEXT_VAR.get() or {}
  entry: dict[str, Any] = {
      "ts": now_iso,
      "timestamp": now_iso,
      "severity": severity,
      "level": lvl,
      "stage": stage,
      "job_id": job_id,
      "item_id": item_id,
      "message": message,
      "formatted": f"[{now_iso}] [{severity}] [{stage}] {message}{extra_str}",
  }
  if extra_dict:
    entry["extra"] = extra_dict
  if trace_ctx.get("trace_id"):
    entry["trace_id"] = trace_ctx["trace_id"]
  if exc is not None:
    entry["error_type"] = type(exc).__name__
    entry["error_message"] = str(exc)
    if is_err_sev:
      entry["traceback"] = tb_str[-4000:]

  # 1. Emit Cloud Logging structured JSON so Cloud Run Logs Explorer indexes severity, stage, trace, extra & stack_trace
  cloud_payload: dict[str, Any] = {
      "severity": severity,
      "time": now_iso,
      "message": formatted_msg,
      "component": "musewave-music-studio",
      "stage": stage,
  }
  if job_id:
    cloud_payload["job_id"] = job_id
  if item_id:
    cloud_payload["item_id"] = item_id
  if extra_dict:
    cloud_payload["extra"] = extra_dict
  if trace_ctx.get("cloud_trace"):
    cloud_payload["logging.googleapis.com/trace"] = trace_ctx["cloud_trace"]
  if trace_ctx.get("span_id"):
    cloud_payload["logging.googleapis.com/spanId"] = trace_ctx["span_id"]
  if exc is not None:
    cloud_payload["error_type"] = type(exc).__name__
    cloud_payload["error_message"] = str(exc)
    if is_err_sev:
      cloud_payload["stack_trace"] = tb_str

  out_stream = sys.stderr if severity in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY") else sys.stdout
  if os.environ.get("K_SERVICE") or os.environ.get("CLOUD_LOGGING_JSON", "true").lower() == "true":
    print(json.dumps(cloud_payload, ensure_ascii=False, default=str), file=out_stream, flush=True)
  else:
    print(entry["formatted"], file=out_stream, flush=True)
    if tb_str:
      print(tb_str, file=out_stream, flush=True)

  # 2. Append to in-memory ring buffer & local `/tmp` log file
  with _LOG_LOCK:
    _RUNTIME_LOGS.append(entry)
    if len(_RUNTIME_LOGS) > _MAX_RUNTIME_LOGS:
      del _RUNTIME_LOGS[: len(_RUNTIME_LOGS) - _MAX_RUNTIME_LOGS]
    try:
      with open(RUNTIME_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
      pass

  return entry


def get_runtime_logs(
    limit: int = 200,
    level: str = "",
    stage: str = "",
    item_id: str = "",
    job_id: str = "",
) -> list[dict[str, Any]]:
  """Returns recent structured runtime logs (newest first)."""
  lvl_filter = (level or "").strip().upper()
  stage_filter = (stage or "").strip().upper()
  item_filter = (item_id or "").strip().lower()
  job_filter = (job_id or "").strip().lower()
  with _LOG_LOCK:
    items = list(reversed(_RUNTIME_LOGS))
  if lvl_filter and lvl_filter != "ALL":
    items = [x for x in items if x.get("severity") == lvl_filter]
  if stage_filter:
    items = [x for x in items if stage_filter in (x.get("stage") or "").upper()]
  if item_filter:
    items = [
        x
        for x in items
        if item_filter in (x.get("item_id") or "").lower()
        or item_filter in (x.get("message") or "").lower()
    ]
  if job_filter:
    items = [x for x in items if job_filter in (x.get("job_id") or "").lower()]
  return items[: max(1, min(1000, int(limit or 200)))]


def clear_runtime_logs() -> int:
  with _LOG_LOCK:
    cnt = len(_RUNTIME_LOGS)
    _RUNTIME_LOGS.clear()
    try:
      if RUNTIME_LOG_FILE.exists():
        RUNTIME_LOG_FILE.unlink(missing_ok=True)
    except Exception:
      pass
  return cnt


# ==============================================================================
# 1. Default System Prompts & Platform Configuration
# ==============================================================================
LYRIA_SUPPORTED_LANGUAGES = [
    "English",
    "Spanish",
    "Japanese",
    "Portuguese",
    "French",
    "Hindi",
    "Korean",
    "German",
]

DEFAULT_LYRIA_COMPILER_SYSTEM_PROMPT = """You are the Studio Master Music Producer & Arrangement Planner (`gemini-4-argon`).
Your mission is to transform a user's music generation request (from Group A: User Lyrics + Style, Group B: Short Prompt -> Vocal Song, or Group C: Pure Instrumental) into an optimal, studio-grade multi-section arrangement plan (`SongPlan`) for Lyria 3.

### KEY ARRANGEMENT & PROMPT ENHANCEMENT RULES:
1. **Self-Contained Per-Section Descriptions**:
   - Avoid relative references like "Same as Verse 1" or "Chorus continues with more drums".
   - Every section's `caption` (`[Intro]`, `[Verse 1]`, `[Chorus]`, `[Bridge]`, `[Outro]`, etc.) MUST be a self-contained, rich English musical description (35-65 words) that explicitly states:
     (a) Core genre & sub-genre aesthetic,
     (b) Tempo feel & groove/BPM,
     (c) Detailed instrumentation & tonal textures active in THIS section,
     (d) Production/mix aesthetic (stereo width, reverb space, warmth/modern punch),
     (e) Tailored vocal delivery, gender, register, and emotional phrasing (or explicit "purely instrumental, no vocals, no singing" if instrumental or an instrumental intro/outro).

2. **Strict Language Separation (Arrangement Captions in English; Lyrics in Target Sung Language)**:
   - All acoustic and arrangement descriptions (`global_caption`, `vocals_caption`, and every section `caption`) MUST be written in rich, professional **English**, even if the user's style tags or vocal gender tags are in another language (e.g., translate Tagalog "Lalaki" -> "Male vocalist", "Babae" -> "Female vocalist", Chinese/French/Spanish style prompts -> English musicological descriptions).
   - All sung `lyrics` MUST remain in the target sung language requested by the user (e.g., English, Spanish, Chinese, Japanese, Portuguese, French, Hindi, Korean, German, Indonesian/Malay).

3. **Mode-Specific Handling (Group A vs. Group B vs. Group C)**:
   - **Group A (`mode = "group_a_lyrics"`, User-Provided Lyrics)**:
     * Preserve the user's lyric lines faithfully (`is_user_provided = true`).
     * Strip structural bracket tags like `[Verse 1]`, `[Chorus]` from the sung lyric lines themselves and use them to segment the song into logical sections (`[Intro]`, `[Verse 1]`, `[Chorus]`, etc.).
   - **Group B (`mode = "group_b_prompt"`, Short Prompt -> Vocal Song)**:
     * Compose original, high-musicality, singable lyrics in the requested `language` with natural meter, rhyme, and emotional imagery across 4 to 7 sections (`Intro`, `Verse 1`, `Pre-Chorus`, `Chorus`, `Verse 2`, `Bridge`, `Outro`).
   - **Group C (`mode = "group_c_instrumental"` or `is_instrumental = true`)**:
     * Set `lyrics` to an empty list `[]` in every section.
     * Set `vocals_caption` to `"none (purely instrumental, no vocals, no singing, no spoken word)"`.
     * Explicitly include `"purely instrumental arrangement, no vocals, no singing"` in every section's `caption` to ensure an instrumental output.

4. **Copyright & Artist-Name Guardrail**:
   - Never output verbatim copyrighted lyrics or famous artist/celebrity names. If the user prompt mentions a specific artist, translate their style into pure acoustic, timbral, and era descriptors.

5. **Section Labels, Duration Bounds & Dedicated `[Outro]` Rule**:
   - Use standard musical section tags (`[Intro]`, `[Verse 1]`, `[Chorus]`, `[Verse 2]`, `[Bridge]`, `[Outro]`).
   - Ensure section `start_sec` and `end_sec` partition `0.0` to `duration_secs` cleanly.
   - **MANDATORY DEDICATED `[Outro]` ANCHOR**: Every full-song plan MUST end with a dedicated, pure-instrumental `[Outro]` section (`lyrics: []`) whose `start_sec` is anchored at `duration_secs - outro_buffer_sec` (typically the final 8–12 seconds) and `end_sec = duration_secs`. All sung lyrics MUST finish BEFORE `outro_start_sec`. Never merge the final vocal chorus and outro into a single section.
   - **Universal Pacing & Density Adaptation**: Follow the `universal_pacing_profile` provided in the input specification (expanding instrumental interludes when user lyrics are sparse, or compacting intro/transitions and using a brisk tempo when user lyrics are dense so vocals never get cut off at the end).

Return ONLY valid JSON matching the requested schema."""

DEFAULT_MUSIC_ANALYSIS_SYSTEM_PROMPT = """You are the Gemini Multimodal Musicologist & Audio Mastering Engineer (`gemini-4-argon`, `thinking_level=medium`).
Listen deeply to the provided audio track and perform a comprehensive structural, acoustic, vocal, lyrical, and production analysis.

Your output MUST be a valid JSON object containing:
1. `title_guess`: A concise, evocative title for the track.
2. `global_summary`: All 16 standardized musical attributes:
   - `music_genre` (Primary & secondary genres)
   - `era_style` (Era aesthetic, e.g., "2020s Modern Synth-Pop / 1980s Retro Revival")
   - `mood` (Primary & secondary emotional moods)
   - `energy_level` ("Low", "Medium", "High", or "Dynamic Arc")
   - `estimated_bpm` (Integer BPM estimate)
   - `estimated_key_and_scale` (e.g., "C Minor", "G Major")
   - `time_signature` (e.g., "4/4", "3/4", "6/8")
   - `instruments` (Array of detected instruments with timbral adjectives)
   - `vocal_presence` ("Vocal", "Instrumental", or "Vocal Chop / Backing Only")
   - `vocal_gender_and_timbre` (Detailed vocal gender, register, breathiness, vibrato, or "None")
   - `detected_language` (Sung language or "Instrumental")
   - `culture_style` (Cultural/regional musical influences)
   - `production_aesthetic` (Mix clarity, stereo imaging, reverb depth, dynamic compression)
   - `harmonic_progression_character` (Chord movement & tension/release notes)
   - `scene_tags` (Array of 4-6 ideal visual/film/use-case scenarios)
   - `one_line_verdict` (Executive producer one-sentence summary)
3. `sections`: Array of chronological song sections covering the full track duration:
   - `section_code` (e.g., "[Intro]", "[Verse 1]", "[Chorus]")
   - `label` (e.g., "Intro", "Verse 1", "Chorus", "Bridge", "Outro")
   - `start_sec` (float)
   - `end_sec` (float)
   - `energy_1_to_10` (integer 1-10)
   - `vocal_state` (e.g., "Instrumental", "Lead Male Vocal", "Full Harmony Chorus")
   - `section_mood` (string)
   - `section_caption_en` (A rich, self-contained 40-word English musical paragraph describing this section's exact instrumentation, mix, and vocal delivery)
   - `transcribed_lyrics` (Array of lines sung in this section with `[MM:SS.S-MM:SS.S]` timestamps, or empty array if instrumental)
4. `production_diagnostics`:
   - `mix_balance_score_10` (float 0-10)
   - `vocal_clarity_score_10` (float 0-10)
   - `structural_coherence_score_10` (float 0-10)
   - `artifact_or_distortion_notes` (Any metallic artifacts, phase issues, lyric slurring, or abrupt cuts)
   - `improvement_suggestions` (Actionable tips to improve the track in Lyria)
5. `reconstructed_lyria3_song_plan_md`: A complete Lyria 3 SongPlan Markdown string (`# Title`, `> global caption`, `> duration: ...`, `> bpm: ...`, `## Section [start-end]`, `> section caption`, `[start-end] lyric`)."""

DEFAULT_MULTI_TRACK_JUDGE_SYSTEM_PROMPT = """You are the Chief Executive Music Producer & Blind/Open Audio Evaluation Jury Chair (`gemini-4-argon`, `thinking_level=medium`).
You are evaluating multiple candidate music tracks (Track 1, Track 2, ...) generated or submitted for the same target specification (Target Prompt, Target Genre/Style, Target Vocal Gender, Target Language, and optional Target User Lyrics).

Listen carefully to EVERY attached audio track from start to finish and compare them rigorously across our 6-Dimension Music Quality Rubric:
- `D1_prompt_and_style_adherence` (Weight 20%): Accuracy to requested genre, style tags, tempo/mood, and instrumental vs. vocal mode.
- `D2_vocal_naturalness_and_emotion` (Weight 20%): Vocal timbre authenticity, breath/phrasing naturalness, gender match, emotional expressiveness (or lead instrument expressiveness if `is_instrumental=1`).
- `D3_lyric_intelligibility_and_prosody` (Weight 15%): Clear pronunciation in target language, adherence to user lyrics (if provided), rhythmic syllable stress, and melodic prosody (if instrumental, evaluate melodic motif memorability).
- `D4_musical_structure_and_progression` (Weight 15%): Clear Intro/Verse/Chorus/Bridge/Outro transitions, hook payoff, dynamic contrast, and absence of repetitive looping or abrupt cutoffs.
- `D5_arrangement_and_instrumentation` (Weight 15%): Richness of accompaniment, instrument separation, rhythmic groove stability, and counterpoint.
- `D6_audio_fidelity_and_mix` (Weight 15%): High-frequency clarity, bass punch, stereo imaging, vocal-to-instrument balance, and absence of diffusion/vocoder artifacts.

Return ONLY a valid JSON object containing:
1. `overall_winner_track_id`: The `track_id` of the best overall track (or `"TIE"`).
2. `executive_summary`: A thorough comparative synthesis explaining why the winner won and how each track performed.
3. `track_evaluations`: Array of objects (one per track, in order):
   - `track_id`: string
   - `track_label`: string
   - `rank`: integer (1 = best)
   - `total_score_100`: float (0-100 weighted total)
   - `dimension_scores`: object with float scores (0.0 to 10.0) for:
     * `D1_prompt_and_style_adherence`
     * `D2_vocal_naturalness_and_emotion`
     * `D3_lyric_intelligibility_and_prosody`
     * `D4_musical_structure_and_progression`
     * `D5_arrangement_and_instrumentation`
     * `D6_audio_fidelity_and_mix`
   - `estimated_duration_sec`: float
   - `detected_genre_and_bpm`: string
   - `strengths`: array of strings
   - `weaknesses`: array of strings
   - `notable_timestamps`: array of strings (e.g., `"01:12 - Strong chorus drop with lush strings"`)
4. `pairwise_comparisons`: Array of head-to-head comparisons between pairs of tracks:
   - `track_a_id`: string
   - `track_b_id`: string
   - `winner_id`: string
   - `margin`: `"Decisive"` | `"Moderate"` | `"Narrow"` | `"Tie"`
   - `key_differentiator`: string
5. `lyria_optimization_recipe`: Actionable recommendation on how to further refine the Lyria SongPlan / section captions to beat all current tracks."""


DEFAULT_PLATFORM_CONFIG = {
    "project_id": (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("CLOUD_ML_PROJECT_ID")
        or "your-gcp-project-id"
    ),
    "location": os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
    "gemini_model": os.environ.get("MUSIC_STUDIO_GEMINI_MODEL", "gemini-4-argon"),
    "gemini_thinking_level": os.environ.get(
        "MUSIC_STUDIO_THINKING_LEVEL", "MEDIUM"
    ),
    "gemini_temperature": 0.4,
    "lyria_default_model": os.environ.get(
        "MUSIC_STUDIO_LYRIA_MODEL", "lyria-3-pro-preview"
    ),
    "lyria_self_healing_enabled": True,
    "lyria_self_healing_max_stages": 3,
    "lyria_min_fullsong_valid_sec": 45.0,
    "duration_strategy": "smart_auto",
    "use_gemini_lyria_compiler": True,
    "enforce_duration_alignment": True,
    "default_outro_buffer_sec": 8.0,
    "auto_tail_fade_guard": True,
    "tail_fade_duration_sec": 1.8,
    "auto_loudnorm_minus14_lufs": False,
    "batch_max_workers": 4,
    "single_parallel_candidates": 1,
    "gcs_bucket_name": os.environ.get(
        "GCS_BUCKET_NAME",
        os.environ.get("MUSIC_STUDIO_GCS_BUCKET", ""),
    ),
    "gcs_object_prefix": os.environ.get("GCS_OBJECT_PREFIX", "music-studio"),
    "gcs_auto_sync": True,
    "pricing": {
        "currency_usd_to_cny": 7.25,
        "gemini_input_per_1m_usd": 1.25,
        "gemini_output_per_1m_usd": 10.00,
        "gemini_thinking_per_1m_usd": 10.00,
        "gemini_audio_tokens_per_sec": 32.0,
        "lyria_billing_mode": "per_track",
        "lyria_pro_per_track_usd": 0.080,
        "lyria_clip_per_track_usd": 0.040,
        "lyria_pro_per_second_usd": 0.0006,
        "lyria_clip_per_second_usd": 0.0013,
        "bill_failed_self_healing_attempts": False,
        "gcs_ops_per_track_usd": 0.0002,
    },
    "prompts": {
        "lyria_compiler_system_prompt": DEFAULT_LYRIA_COMPILER_SYSTEM_PROMPT,
        "music_analysis_system_prompt": DEFAULT_MUSIC_ANALYSIS_SYSTEM_PROMPT,
        "multi_track_judge_system_prompt": (
            DEFAULT_MULTI_TRACK_JUDGE_SYSTEM_PROMPT
        ),
    },
}


class ConfigStore:
  """Thread-safe runtime configuration manager persisted to platform_config.json."""

  def __init__(self, path: Path):
    self.path = path
    self._lock = threading.Lock()
    self._config = json.loads(json.dumps(DEFAULT_PLATFORM_CONFIG))
    self._load()

  def _load(self):
    if self.path.exists():
      try:
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        for k, v in saved.items():
          if k in ("prompts", "pricing") and isinstance(v, dict):
            self._config.setdefault(k, {}).update(v)
          else:
            self._config[k] = v
      except Exception as e:
        print(f"[ConfigStore] Warning: failed to load {self.path}: {e}")

  def get(self) -> dict:
    with self._lock:
      return json.loads(json.dumps(self._config))

  def update(self, patch: dict) -> dict:
    with self._lock:
      for k, v in patch.items():
        if k in ("prompts", "pricing") and isinstance(v, dict):
          self._config.setdefault(k, {}).update(v)
        elif v is not None:
          self._config[k] = v
      self.path.write_text(
          json.dumps(self._config, ensure_ascii=False, indent=2),
          encoding="utf-8",
      )
      return json.loads(json.dumps(self._config))

  def reset(self) -> dict:
    with self._lock:
      self._config = json.loads(json.dumps(DEFAULT_PLATFORM_CONFIG))
      if self.path.exists():
        self.path.unlink()
      return json.loads(json.dumps(self._config))


CONFIG_STORE = ConfigStore(CONFIG_FILE_PATH)


# ==============================================================================
# 2. Configurable Google Cloud Storage (GCS) Manager
# ==============================================================================
class GCSStorageManager:
  """Manages uploading, listing, and downloading studio artifacts in a configurable GCS bucket.

  Supports both `google-cloud-storage` SDK and a zero-dependency REST fallback
  using `google.auth` + `urllib.request`.
  """

  def __init__(self, config_store: ConfigStore):
    self.config_store = config_store

  @property
  def bucket_name(self) -> str:
    cfg = self.config_store.get()
    b = (cfg.get("gcs_bucket_name") or "").strip()
    if b.startswith("gs://"):
      b = b[5:].strip("/")
    return b

  @property
  def prefix(self) -> str:
    cfg = self.config_store.get()
    return (cfg.get("gcs_object_prefix") or "music-studio").strip("/")

  @property
  def enabled(self) -> bool:
    cfg = self.config_store.get()
    return bool(self.bucket_name) and bool(cfg.get("gcs_auto_sync", True))

  def _object_name(self, rel_path: str) -> str:
    rel = rel_path.lstrip("/")
    return f"{self.prefix}/{rel}" if self.prefix else rel

  def _get_auth_token(self) -> str:
    if not HAS_GOOGLE_AUTH:
      raise RuntimeError("google-auth is not installed")
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/devstorage.full_control"]
    )
    auth_req = google.auth.transport.requests.Request()
    creds.refresh(auth_req)
    return creds.token

  def upload_file(
      self,
      local_path: Path,
      rel_object_path: str,
      content_type: str = "application/octet-stream",
      force: bool = False,
  ) -> dict:
    """Uploads a local file to `gs://<bucket>/<prefix>/<rel_object_path>`."""
    bucket = self.bucket_name
    if not bucket:
      return {"uploaded": False, "reason": "No GCS bucket configured"}
    if not force and not self.enabled:
      return {"uploaded": False, "reason": "GCS auto-sync disabled"}
    if not local_path.exists():
      return {"uploaded": False, "reason": f"Local file missing: {local_path}"}

    obj_name = self._object_name(rel_object_path)
    gs_uri = f"gs://{bucket}/{obj_name}"
    public_url = f"https://storage.cloud.google.com/{bucket}/{urllib.parse.quote(obj_name)}"

    # Method 1: google-cloud-storage SDK
    if HAS_GCS_SDK:
      try:
        cfg = self.config_store.get()
        client = gcs_storage.Client(project=cfg.get("project_id") or None)
        b_obj = client.bucket(bucket)
        blob = b_obj.blob(obj_name)
        blob.upload_from_filename(str(local_path), content_type=content_type)
        return {
            "uploaded": True,
            "bucket": bucket,
            "object_name": obj_name,
            "gs_uri": gs_uri,
            "authenticated_url": public_url,
            "size_bytes": local_path.stat().st_size,
        }
      except Exception as sdk_err:
        print(f"[GCS] SDK upload fallback to REST ({sdk_err})")

    # Method 2: Zero-dependency JSON API REST upload
    try:
      token = self._get_auth_token()
      encoded_name = urllib.parse.quote(obj_name, safe="")
      url = (
          f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
          f"?uploadType=media&name={encoded_name}"
      )
      data = local_path.read_bytes()
      req = urllib.request.Request(
          url,
          data=data,
          method="POST",
          headers={
              "Authorization": f"Bearer {token}",
              "Content-Type": content_type,
              "Content-Length": str(len(data)),
          },
      )
      with urllib.request.urlopen(req, timeout=60) as resp:
        _ = resp.read()
      return {
          "uploaded": True,
          "bucket": bucket,
          "object_name": obj_name,
          "gs_uri": gs_uri,
          "authenticated_url": public_url,
          "size_bytes": len(data),
      }
    except Exception as rest_err:
      return {
          "uploaded": False,
          "bucket": bucket,
          "object_name": obj_name,
          "gs_uri": gs_uri,
          "error": str(rest_err),
      }

  def upload_json(
      self, payload: dict | list, rel_object_path: str, force: bool = False
  ) -> dict:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", encoding="utf-8", delete=False
    ) as tmp:
      json.dump(payload, tmp, ensure_ascii=False, indent=2)
      tmp_path = Path(tmp.name)
    try:
      return self.upload_file(
          tmp_path,
          rel_object_path,
          content_type="application/json; charset=utf-8",
          force=force,
      )
    finally:
      tmp_path.unlink(missing_ok=True)

  @staticmethod
  def parse_gs_uri(uri_or_path: str, default_bucket: str = "") -> tuple[str, str]:
    """Parses a `gs://<bucket>/<object>`, `https://storage.cloud.google.com/<bucket>/<object>`,

    `/api/gcs/stream?uri=...`, or `<bucket>/<object>` string into `(bucket_name, object_path)`.
    """
    raw = (uri_or_path or "").strip()
    if not raw:
      return default_bucket, ""
    if "/api/gcs/stream" in raw and "?" in raw:
      qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw).query)
      inner = (qs.get("uri") or [""])[0]
      if inner:
        raw = inner.strip()
    if raw.startswith("gs://"):
      rest = raw[5:].strip("/")
      if "/" in rest:
        b, obj = rest.split("/", 1)
        return b.strip(), obj.lstrip("/")
      return rest.strip(), ""
    for host_prefix in (
        "https://storage.cloud.google.com/",
        "https://storage.googleapis.com/",
    ):
      if raw.startswith(host_prefix):
        rest = raw[len(host_prefix) :].split("?", 1)[0].strip("/")
        if "/" in rest:
          b, obj = rest.split("/", 1)
          return b.strip(), urllib.parse.unquote(obj.lstrip("/"))
        return rest.strip(), ""
    return default_bucket, raw.lstrip("/")

  def list_objects(
      self,
      sub_prefix: str = "",
      max_results: int = 250,
      bucket_override: str = "",
      raw_prefix: str | None = None,
  ) -> dict:
    bucket = (bucket_override or "").strip()
    if bucket.startswith("gs://"):
      parsed_b, parsed_p = self.parse_gs_uri(bucket)
      bucket = parsed_b
      if raw_prefix is None and parsed_p:
        raw_prefix = parsed_p
    if not bucket:
      bucket = self.bucket_name
    if not bucket:
      return {
          "ok": False,
          "bucket": "",
          "items": [],
          "reason": "No GCS bucket configured",
      }
    if raw_prefix is not None:
      full_prefix = raw_prefix.lstrip("/")
    elif sub_prefix.startswith("gs://"):
      parsed_b, parsed_p = self.parse_gs_uri(sub_prefix, default_bucket=bucket)
      bucket = parsed_b or bucket
      full_prefix = parsed_p
    else:
      full_prefix = (
          self._object_name(sub_prefix)
          if sub_prefix
          else (f"{self.prefix}/" if self.prefix else "")
      )
    items = []
    if HAS_GCS_SDK:
      try:
        cfg = self.config_store.get()
        client = gcs_storage.Client(project=cfg.get("project_id") or None)
        blobs = client.list_blobs(
            bucket, prefix=full_prefix, max_results=max_results
        )
        for b in blobs:
          if b.name.endswith("/"):
            continue
          gs_uri = f"gs://{bucket}/{b.name}"
          items.append({
              "name": b.name,
              "filename": Path(b.name).name,
              "gs_uri": gs_uri,
              "stream_url": f"/api/gcs/stream?uri={urllib.parse.quote(gs_uri, safe='')}",
              "authenticated_url": f"https://storage.cloud.google.com/{bucket}/{urllib.parse.quote(b.name)}",
              "size_bytes": b.size or 0,
              "content_type": b.content_type or "",
              "updated": b.updated.isoformat() if b.updated else "",
          })
        return {
            "ok": True,
            "bucket": bucket,
            "prefix": full_prefix,
            "items": items,
        }
      except Exception as e:
        print(f"[GCS] SDK list_blobs fallback to REST: {e}")

    try:
      token = self._get_auth_token()
      q_params = {"maxResults": str(max_results)}
      if full_prefix:
        q_params["prefix"] = full_prefix
      q = urllib.parse.urlencode(q_params)
      url = f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?{q}"
      req = urllib.request.Request(
          url, headers={"Authorization": f"Bearer {token}"}
      )
      with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
      for it in data.get("items", []):
        name = it.get("name", "")
        if not name or name.endswith("/"):
          continue
        gs_uri = f"gs://{bucket}/{name}"
        items.append({
            "name": name,
            "filename": Path(name).name,
            "gs_uri": gs_uri,
            "stream_url": f"/api/gcs/stream?uri={urllib.parse.quote(gs_uri, safe='')}",
            "authenticated_url": (
                f"https://storage.cloud.google.com/{bucket}/"
                f"{urllib.parse.quote(name)}"
            ),
            "size_bytes": int(it.get("size", 0)),
            "content_type": it.get("contentType", ""),
            "updated": it.get("updated", ""),
        })
      return {
          "ok": True,
          "bucket": bucket,
          "prefix": full_prefix,
          "items": items,
      }
    except Exception as e:
      return {
          "ok": False,
          "bucket": bucket,
          "prefix": full_prefix,
          "items": [],
          "error": str(e),
      }

  def download_arbitrary_gs_uri(
      self, gs_uri: str, local_dst: Path | None = None
  ) -> Path:
    """Downloads any `gs://<bucket>/<object>` (or storage URL) to `local_dst` or `GCS_CACHE_DIR`."""
    bucket, obj_name = self.parse_gs_uri(gs_uri, default_bucket=self.bucket_name)
    if not bucket or not obj_name:
      raise ValueError(f"Invalid GCS URI: {gs_uri}")

    if local_dst is None:
      uri_hash = hashlib.sha256(
          f"{bucket}/{obj_name}".encode("utf-8")
      ).hexdigest()[:16]
      safe_fname = re.sub(r"[^a-zA-Z0-9._-]", "_", Path(obj_name).name)
      local_dst = GCS_CACHE_DIR / f"{uri_hash}_{safe_fname}"

    local_dst.parent.mkdir(parents=True, exist_ok=True)
    if local_dst.exists() and local_dst.stat().st_size > 128:
      return local_dst

    if HAS_GCS_SDK:
      try:
        cfg = self.config_store.get()
        client = gcs_storage.Client(project=cfg.get("project_id") or None)
        b_obj = client.bucket(bucket)
        blob = b_obj.blob(obj_name)
        if blob.exists():
          blob.download_to_filename(str(local_dst))
          if local_dst.exists() and local_dst.stat().st_size > 0:
            return local_dst
      except Exception as e:
        print(f"[GCS] SDK download_arbitrary_gs_uri fallback to REST: {e}")

    token = self._get_auth_token()
    encoded_name = urllib.parse.quote(obj_name, safe="")
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{encoded_name}?alt=media"
    )
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
      local_dst.write_bytes(resp.read())
    if not local_dst.exists() or local_dst.stat().st_size == 0:
      raise FileNotFoundError(f"Empty or missing GCS object: gs://{bucket}/{obj_name}")
    return local_dst

  def read_arbitrary_gs_bytes(self, gs_uri: str) -> bytes:
    """Reads raw bytes directly from any `gs://<bucket>/<object>` URI."""
    bucket, obj_name = self.parse_gs_uri(gs_uri, default_bucket=self.bucket_name)
    if not bucket or not obj_name:
      raise ValueError(f"Invalid GCS URI: {gs_uri}")

    if HAS_GCS_SDK:
      try:
        cfg = self.config_store.get()
        client = gcs_storage.Client(project=cfg.get("project_id") or None)
        b_obj = client.bucket(bucket)
        blob = b_obj.blob(obj_name)
        if blob.exists():
          return blob.download_as_bytes()
      except Exception as e:
        print(f"[GCS] SDK read_arbitrary_gs_bytes fallback to REST: {e}")

    token = self._get_auth_token()
    encoded_name = urllib.parse.quote(obj_name, safe="")
    url = (
        f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/"
        f"{encoded_name}?alt=media"
    )
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
      return resp.read()

  def download_file(self, rel_object_path: str, local_dst: Path) -> bool:
    """Downloads an object from `gs://<bucket>/<prefix>/<rel_object_path>` to `local_dst`."""
    bucket = self.bucket_name
    if not bucket:
      return False
    obj_name = self._object_name(rel_object_path)
    try:
      self.download_arbitrary_gs_uri(f"gs://{bucket}/{obj_name}", local_dst)
      return local_dst.exists() and local_dst.stat().st_size > 0
    except Exception as e:
      print(f"[GCS] download_file failed for {obj_name}: {e}")
      return False

  def download_json(self, rel_object_path: str) -> dict | None:
    """Downloads a JSON object from GCS directly into memory without touching local/FUSE disk.

    Eliminates GCS FUSE `SetInodeAttributes: stale file handle` clobbering when
    secondary Cloud Run instances poll batch job status.
    """
    bucket = self.bucket_name
    if not bucket:
      return None
    obj_name = self._object_name(rel_object_path)
    try:
      raw = self.read_arbitrary_gs_bytes(f"gs://{bucket}/{obj_name}")
      return json.loads(raw.decode("utf-8"))
    except Exception:
      return None

  @staticmethod
  def _guess_item_id_from_filename(fname: str) -> str:
    """Infers an item_id (e.g., `A_A01`, `A01`, `B12`, `C05`, or stem) from a GCS audio filename."""
    stem = Path(fname).stem
    # Match `gen_A_A01_20261027_...` or `gen_A01_20261027_...`
    m_gen = re.match(r"^gen_([A-Za-z0-9_-]+?)_\d{8}_", stem)
    if m_gen:
      return m_gen.group(1)
    m_full = re.search(
        r"(?:^|[_-])([ABC]_[ABC]\d{2,3})(?:[_-]|$)", stem, re.IGNORECASE
    )
    if m_full:
      return m_full.group(1).upper()
    m_abc = re.search(r"(?:^|[_-])([ABC]\d{2,3})(?:[_-]|$)", stem, re.IGNORECASE)
    if m_abc:
      return m_abc.group(1).upper()
    return stem[:32]

  def discover_assets(
      self,
      bucket_override: str = "",
      raw_prefix: str | None = None,
      scan_entire_bucket_if_empty: bool = True,
      auto_register: bool = True,
      auto_load_dataset: bool = True,
      force_reload_dataset: bool = False,
      max_results: int = 500,
  ) -> dict:
    """Dynamically scans GCS for all audio files, datasets/prompt-lists, and studio metadata.

    Works for both Studio-generated files AND raw/unindexed `.mp3/.wav/.flac/.csv/.xlsx`
    assets dropped directly into GCS without sidecar `.json` files.
    """
    listed = self.list_objects(
        bucket_override=bucket_override,
        raw_prefix=raw_prefix,
        max_results=max_results,
    )
    if not listed.get("ok"):
      return listed

    items = listed.get("items", [])
    used_prefix = listed.get("prefix", "")
    bucket = listed.get("bucket", "")

    # If the configured prefix returned no audio/dataset objects and whole-bucket fallback is allowed,
    # automatically scan the root of the bucket (`raw_prefix=""`) so assets in any folder are discovered!
    if (
        not items
        and scan_entire_bucket_if_empty
        and raw_prefix is None
        and used_prefix
    ):
      fallback_listed = self.list_objects(
          bucket_override=bucket, raw_prefix="", max_results=max_results
      )
      if fallback_listed.get("ok") and fallback_listed.get("items"):
        listed = fallback_listed
        items = fallback_listed.get("items", [])
        used_prefix = ""

    audio_exts = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}
    tabular_exts = {".csv", ".tsv", ".xlsx", ".txt"}
    internal_json_prefixes = (
        "gen_",
        "up_",
        "ana_",
        "eval_",
        "batch_",
        "plist_",
        "gcs_ping",
        "platform_config",
    )

    audio_assets = []
    dataset_assets = []
    metadata_jsons = []

    for it in items:
      obj_name = it.get("name", "")
      fname = Path(obj_name).name
      ext = Path(fname).suffix.lower()
      gs_uri = it.get("gs_uri") or f"gs://{bucket}/{obj_name}"
      stream_url = f"/api/gcs/stream?uri={urllib.parse.quote(gs_uri, safe='')}"

      if ext in audio_exts:
        cat = (
            "generated"
            if "/generations/" in f"/{obj_name}" or fname.startswith("gen_")
            else (
                "uploaded"
                if "/uploads/" in f"/{obj_name}" or fname.startswith("up_")
                else "raw_gcs_asset"
            )
        )
        item_guess = self._guess_item_id_from_filename(fname)
        audio_assets.append({
            **it,
            "filename": fname,
            "gs_uri": gs_uri,
            "stream_url": stream_url,
            "audio_url": stream_url,
            "category": cat,
            "item_id_guess": item_guess,
        })
      elif ext in tabular_exts:
        dataset_assets.append({
            **it,
            "filename": fname,
            "gs_uri": gs_uri,
            "format": ext.lstrip(".").upper(),
        })
      elif ext == ".json":
        if fname.startswith(internal_json_prefixes) or "/batch_jobs/" in f"/{obj_name}" or "/health/" in f"/{obj_name}":
          metadata_jsons.append(it)
        else:
          dataset_assets.append({
              **it,
              "filename": fname,
              "gs_uri": gs_uri,
              "format": "JSON",
          })

    registered_raw_audio = 0
    if auto_register and audio_assets:
      # Collect already-known audio filenames/URIs so we don't duplicate existing records
      known_uris = set()
      known_fnames = set()
      for folder, pat in [(GENERATED_DIR, "gen_*.json"), (UPLOADS_AUDIO_DIR, "up_*.json")]:
        for jp in folder.glob(pat):
          try:
            rec = json.loads(jp.read_text(encoding="utf-8"))
            aud = rec.get("audio") or rec
            if aud.get("filename"):
              known_fnames.add(aud["filename"])
            if aud.get("gcs", {}).get("gs_uri"):
              known_uris.add(aud["gcs"]["gs_uri"])
          except Exception:
            pass

      for a_it in audio_assets:
        gs_uri = a_it["gs_uri"]
        fname = a_it["filename"]
        if gs_uri in known_uris or fname in known_fnames:
          continue
        uri_hash = hashlib.sha256(gs_uri.encode("utf-8")).hexdigest()[:8]
        # Register as a discovered upload/asset record so it appears in Library, Analysis & Arena
        up_id = f"up_gcs_{uri_hash}"
        up_json_path = UPLOADS_AUDIO_DIR / f"{up_id}.json"
        if not up_json_path.exists():
          up_rec = {
              "upload_id": up_id,
              "original_filename": f"[GCS] {a_it['name']}",
              "filename": fname,
              "audio_url": a_it["stream_url"],
              "gs_uri": gs_uri,
              "item_id_guess": a_it["item_id_guess"],
              "mime_type": a_it.get("content_type") or "audio/mpeg",
              "probe": {"duration_sec": 0.0, "size_bytes": a_it.get("size_bytes", 0)},
              "gcs": {
                  "uploaded": True,
                  "bucket": bucket,
                  "object_name": a_it["name"],
                  "gs_uri": gs_uri,
                  "authenticated_url": a_it.get("authenticated_url", ""),
                  "size_bytes": a_it.get("size_bytes", 0),
              },
              "uploaded_at": a_it.get("updated") or datetime.now(timezone.utc).isoformat(),
              "discovered_from_gcs": True,
          }
          try:
            up_json_path.write_text(
                json.dumps(up_rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            registered_raw_audio += 1
          except Exception:
            pass

        # If this raw audio file in GCS looks like a generated track (`gen_<item_id>_...mp3`)
        # whose `.json` metadata was missing locally, also synthesize a generation record!
        stem = Path(fname).stem
        if stem.startswith("gen_") and not (GENERATED_DIR / f"{stem}.json").exists():
          iid = a_it["item_id_guess"]
          grp = iid[0].upper() if iid and iid[0].upper() in ("A", "B", "C") else "U"
          gen_rec = {
              "generation_id": stem,
              "item_id": iid,
              "group": grp,
              "created_at": a_it.get("updated") or datetime.now(timezone.utc).isoformat(),
              "compile_only": False,
              "mode": "group_c_instrumental" if grp == "C" else "group_a_lyrics",
              "lyria_model": "lyria-3-pro-preview (GCS Discovered)",
              "use_gemini_compiler": True,
              "parallel_candidates_count": 1,
              "input": {"user_prompt": f"Discovered from {gs_uri}"},
              "song_plan": None,
              "prompt_for_lyria": "",
              "audio": {
                  "filename": fname,
                  "audio_url": a_it["stream_url"],
                  "duration_sec": 0.0,
                  "used_route": "gcs-discovered",
                  "variant_id": "GCS_Track",
                  "variant_label": f"GCS 发现音频 ({fname})",
                  "gcs": {
                      "uploaded": True,
                      "bucket": bucket,
                      "object_name": a_it["name"],
                      "gs_uri": gs_uri,
                      "authenticated_url": a_it.get("authenticated_url", ""),
                      "size_bytes": a_it.get("size_bytes", 0),
                  },
              },
          }
          try:
            (GENERATED_DIR / f"{stem}.json").write_text(
                json.dumps(gen_rec, ensure_ascii=False, indent=2), encoding="utf-8"
            )
          except Exception:
            pass

    auto_loaded_dataset_items = 0
    auto_loaded_dataset_source = ""
    if auto_load_dataset and dataset_assets and (
        force_reload_dataset or not BUILTIN_DATASET.get("items")
    ):
      # Prefer a master .xlsx or .json dataset if available, otherwise combine discovered .csv/.tsv/.txt files
      xlsx_or_json = [
          d for d in dataset_assets if d.get("format") in ("XLSX", "JSON")
      ]
      targets_to_load = xlsx_or_json[:1] if xlsx_or_json else dataset_assets[:5]
      combined_items = []
      loaded_uris = []
      for d_asset in targets_to_load:
        try:
          raw_b = self.read_arbitrary_gs_bytes(d_asset["gs_uri"])
          parsed = parse_uploaded_prompt_list(d_asset["filename"], raw_b)
          parent_dir = str(Path(d_asset.get("name", "")).parent).strip(".")
          for it in parsed:
            ref_u = (it.get("result_audio_url") or "").strip()
            if ref_u and not ref_u.startswith(
                ("http://", "https://", "/api/", "gs://")
            ):
              rel_obj = (
                  f"{parent_dir}/{ref_u}".lstrip("/")
                  if parent_dir
                  else ref_u.lstrip("/")
              )
              full_gs = f"gs://{bucket}/{rel_obj}"
              it["gcs_reference_uri"] = full_gs
              it["result_audio_url"] = (
                  f"/api/gcs/stream?uri={urllib.parse.quote(full_gs, safe='')}"
              )
          combined_items.extend(parsed)
          loaded_uris.append(d_asset["gs_uri"])
        except Exception as e:
          print(f"[GCS] Auto-load dataset skipped for {d_asset.get('gs_uri')}: {e}")
      if combined_items:
        auto_loaded_dataset_source = ", ".join(loaded_uris)
        _apply_items_to_builtin_dataset(
            combined_items,
            source_label=auto_loaded_dataset_source,
            audio_assets=audio_assets,
        )
        auto_loaded_dataset_items = len(combined_items)

    return {
        "ok": True,
        "bucket": bucket,
        "prefix": used_prefix,
        "total_objects_scanned": len(items),
        "audio_count": len(audio_assets),
        "dataset_count": len(dataset_assets),
        "metadata_json_count": len(metadata_jsons),
        "registered_raw_audio": registered_raw_audio,
        "auto_loaded_dataset_items": auto_loaded_dataset_items,
        "auto_loaded_dataset_source": auto_loaded_dataset_source,
        "active_dataset_total": BUILTIN_DATASET.get("total_count", 0),
        "audio_assets": audio_assets,
        "dataset_assets": dataset_assets,
    }

  def sync_metadata_from_gcs(self) -> dict:
    """Pulls any missing generation/upload/analysis/evaluation/job JSON records from GCS

    AND automatically discovers any raw audio files (`.mp3/.wav/.flac`) in the bucket.
    """
    bucket = self.bucket_name
    if not bucket:
      return {"synced": 0, "discovered_audio": 0, "reason": "No GCS bucket configured"}

    tmp_jobs_dir = Path(tempfile.gettempdir()) / "music_studio_jobs_cache"
    tmp_jobs_dir.mkdir(parents=True, exist_ok=True)
    folder_map = {
        "generations/": GENERATED_DIR,
        "uploads/audio/": UPLOADS_AUDIO_DIR,
        "uploads/prompt_lists/": UPLOADS_LISTS_DIR,
        "analyses/": ANALYSES_DIR,
        "evaluations/": EVALUATIONS_DIR,
        "batch_jobs/": tmp_jobs_dir,
    }
    synced_count = 0
    for sub_p, local_dir in folder_map.items():
      listed = self.list_objects(sub_prefix=sub_p, max_results=300)
      for it in listed.get("items", []):
        obj_name = it.get("name", "")
        fname = Path(obj_name).name
        if not fname.endswith(".json"):
          continue
        dst = local_dir / fname
        if not dst.exists() or dst.stat().st_size < 10:
          rel_path = f"{sub_p}{fname}"
          if self.download_file(rel_path, dst):
            synced_count += 1

    disc = self.discover_assets(
        bucket_override=bucket,
        raw_prefix=None,
        scan_entire_bucket_if_empty=True,
        auto_register=True,
    )
    return {
        "synced": synced_count,
        "discovered_audio": disc.get("audio_count", 0),
        "registered_raw_audio": disc.get("registered_raw_audio", 0),
        "discovered_datasets": disc.get("dataset_count", 0),
        "bucket": bucket,
    }


GCS_MANAGER = GCSStorageManager(CONFIG_STORE)


# ==============================================================================
# 3. Dataset Loader & Custom Prompt List Parser (CSV / XLSX / JSON / TXT)
# ==============================================================================
def _col_to_idx(col_str: str) -> int:
  idx = 0
  for ch in col_str:
    if "A" <= ch <= "Z":
      idx = idx * 26 + (ord(ch) - ord("A") + 1)
  return idx - 1


def parse_xlsx_bytes(raw_bytes: bytes) -> dict[str, list[list[str]]]:
  """Zero-dependency .xlsx parser for uploaded Excel prompt lists."""
  ns = {
      "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
  }
  with zipfile.ZipFile(io.BytesIO(raw_bytes), "r") as zf:
    shared_strings = []
    if "xl/sharedStrings.xml" in zf.namelist():
      ss_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
      for si in ss_root.findall("main:si", ns):
        texts = [t.text or "" for t in si.findall(".//main:t", ns)]
        shared_strings.append("".join(texts))

    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root}

    sheets_data = {}
    for sheet in wb_root.findall(".//main:sheet", ns):
      name = sheet.attrib["name"]
      rid = sheet.attrib[
          "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
      ]
      target = rel_map[rid].lstrip("/")
      if not target.startswith("xl/"):
        target = "xl/" + target
      ws_root = ET.fromstring(zf.read(target))
      rows_out = []
      for row in ws_root.findall(".//main:sheetData/main:row", ns):
        cells = {}
        max_col = -1
        for c in row.findall("main:c", ns):
          ref = c.attrib.get("r", "")
          m = re.match(r"([A-Z]+)", ref)
          col_i = _col_to_idx(m.group(1)) if m else (max_col + 1)
          max_col = max(max_col, col_i)
          ctype = c.attrib.get("t", "")
          v_el = c.find("main:v", ns)
          is_el = c.find("main:is", ns)
          val = ""
          if ctype == "s" and v_el is not None and v_el.text is not None:
            s_idx = int(v_el.text)
            val = (
                shared_strings[s_idx] if 0 <= s_idx < len(shared_strings) else ""
            )
          elif ctype == "inlineStr" and is_el is not None:
            val = "".join(t.text or "" for t in is_el.findall(".//main:t", ns))
          elif v_el is not None and v_el.text is not None:
            val = v_el.text
          cells[col_i] = str(val).strip()
        if max_col >= 0:
          row_list = [cells.get(i, "") for i in range(max_col + 1)]
          if any(row_list):
            rows_out.append(row_list)
      sheets_data[name] = rows_out
  return sheets_data


def estimate_optimal_song_duration_sec(
    user_lyrics: str = "",
    user_prompt: str = "",
    genre: str = "Pop",
    raw_style: str = "",
    is_instrumental: bool = False,
    requested_duration_raw: str | float | None = None,
    requested_duration_param: str | float | None = None,
    reference_duration_sec: float = 0.0,
    duration_strategy: str | None = None,
    group: str = "",
    mode: str = "",
    **_extra_kwargs,
) -> float:
  """Computes an optimal, natural target duration (in seconds) for Lyria 3 Pro (`[60.0, 180.0]`).

  1. If `requested_duration_raw` (or `requested_duration_param`) is an explicit positive number
     (not `'auto'` or empty), clamps and returns that exact requested duration in `[60.0, 180.0]`.
  2. If `duration_strategy == 'match_reference'` and `reference_duration_sec > 0`,
     returns `reference_duration_sec` clamped to `[60.0, 180.0]`.
  3. Otherwise (`'smart_auto'`, default): derives a song-specific target duration from
     actual lyric line/character density (Group A) or genre/tempo feel (Group B & C),
     preventing all tracks with external 3-4 min reference audio from clustering at 180s.
  """
  eff_req = (
      requested_duration_raw
      if requested_duration_raw is not None
      else requested_duration_param
  )
  if eff_req is not None:
    s_req = str(eff_req).strip().lower()
    if s_req and s_req not in ("auto", "none", "null", "nan", "0", "0.0"):
      try:
        val = float(s_req)
        if val > 0:
          return max(60.0, min(180.0, round(val, 1)))
      except ValueError:
        pass

  strat = (
      duration_strategy
      or CONFIG_STORE.get().get("duration_strategy")
      or "smart_auto"
  ).strip().lower()
  if strat == "match_reference" and reference_duration_sec and reference_duration_sec > 0:
    return max(60.0, min(180.0, round(float(reference_duration_sec), 1)))

  style_blob = f"{genre or ''} {raw_style or ''} {user_prompt or ''}".lower()

  # Case C: Pure Instrumental
  if is_instrumental:
    if any(k in style_blob for k in ("classical", "orchestra", "symphon", "cinematic", "epic", "ambient", "soundtrack")):
      return 145.0
    if any(k in style_blob for k in ("lofi", "lo-fi", "beat", "edm", "electronic", "synth", "funk")):
      return 125.0
    return 135.0

  clean_lines = [
      ln.strip()
      for ln in (user_lyrics or "").splitlines()
      if ln.strip() and not re.match(r"^\s*[\[\(].*?[\]\)]\s*$", ln.strip())
  ]

  # Case A: Vocal song with user-provided lyrics
  if clean_lines:
    est_singing_sec = 0.0
    for ln in clean_lines:
      cjk_chars = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", ln))
      non_cjk_words = len(
          re.findall(
              r"[A-Za-zÀ-ÖØ-öø-ÿА-Яа-я0-9]+",
              re.sub(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", " ", ln),
          )
      )
      line_sec = max(2.6, min(6.0, cjk_chars * 0.27 + non_cjk_words * 0.40 + 0.85))
      est_singing_sec += line_sec

    vocal_span = est_singing_sec * 1.15
    arrangement_overhead = 36.0 if len(clean_lines) <= 24 else 28.0
    raw_target = vocal_span + arrangement_overhead
    # Snap to nearest 5 seconds within [95.0, 175.0]
    snapped = round(raw_target / 5.0) * 5.0
    return max(95.0, min(175.0, float(snapped)))

  # Case B: Vocal song from short prompt (auto-generated lyrics)
  if any(k in style_blob for k in ("rap", "hip-hop", "hip hop", "trap", "punk", "fast", "upbeat", "dance", "edm")):
    return 130.0
  if any(k in style_blob for k in ("ballad", "r&b", "soul", "slow", "emotional", "rock", "anthem", "jazz")):
    return 155.0
  return 145.0


def normalize_prompt_item(
    raw: dict, idx: int = 1, default_group: str = "U"
) -> dict:
  """Normalizes an arbitrary row dict from CSV/XLSX/JSON into a standard Music Studio item."""
  # Flexible column lookup
  def pick(*keys, default=""):
    for k in keys:
      for rk, rv in raw.items():
        if rk.strip().lower() == k.lower() and rv is not None:
          s = str(rv).strip()
          if s and s not in ("None", "nan", "NaN"):
            return s
    return default

  group = pick("group", "组别", default=default_group)
  seq = pick("seq", "序号", "index", default=str(idx))
  item_id = pick(
      "id",
      "item_id",
      default=(
          f"{group}{int(seq):02d}" if str(seq).isdigit() else f"{group}_{idx}"
      ),
  )
  user_lyrics = pick(
      "user_lyrics",
      "lyrics",
      "用户歌词(用户手填)",
      "用户歌词",
      "歌词",
  )
  user_prompt = pick(
      "user_prompt",
      "prompt",
      "用户提示词(原始：风格描述+人声)",
      "用户提示词(原始)",
      "用户提示词",
      "提示词",
      "style_prompt",
      "description",
  )
  genre = pick(
      "genre", "音乐风格(归一为9类)", "音乐风格", "style", "风格", default="Pop"
  )
  raw_style = pick("raw_style", "原始style", default=genre)
  language = pick("language", "语言", "lang", default="English")
  vocal_gender = pick(
      "vocal_gender", "人声性别(vocal_gender)", "人声性别", "gender", default=""
  )
  is_inst_str = pick(
      "is_instrumental", "instrumental", "是否纯音乐", default=""
  )
  if is_inst_str in ("1", "True", "true", "是", "yes") or group == "C":
    is_instrumental = 1
  else:
    is_instrumental = 0

  # Auto-infer group (A/B/C) if seq/item_id starts with A/B/C
  if group == "U":
    for cand_s in (str(seq), str(item_id)):
      m_grp = re.match(r"^([ABC])(?:_?[ABC])?\d+", cand_s.strip(), re.IGNORECASE)
      if m_grp:
        group = m_grp.group(1).upper()
        break

  # Separate explicit requested duration parameter (`请求时长参数_秒(duration)`) from
  # external reference audio measured duration (`结果音频时长_秒(实测)`)
  req_dur_str = pick(
      "target_duration_sec",
      "requested_duration_sec",
      "请求时长参数_秒(duration)",
      "duration_sec",
      "duration",
      default="auto",
  )
  ref_dur_str = pick(
      "actual_duration_sec",
      "reference_duration_sec",
      "结果音频时长_秒(实测)",
      default="",
  )
  try:
    ref_dur_val = float(ref_dur_str) if ref_dur_str not in ("auto", "") else 0.0
  except ValueError:
    ref_dur_val = 0.0

  target_dur_val = estimate_optimal_song_duration_sec(
      user_lyrics=user_lyrics,
      user_prompt=user_prompt,
      genre=genre,
      raw_style=raw_style,
      is_instrumental=bool(is_instrumental),
      requested_duration_raw=req_dur_str,
      reference_duration_sec=ref_dur_val,
  )
  # Preserve reference audio measured duration for display if present, otherwise show target_dur_val
  display_ref_dur = round(ref_dur_val, 2) if ref_dur_val > 0 else target_dur_val

  raw_audio_url = pick(
      "result_audio_url",
      "结果音频URL",
      "audio_url",
      "gcs_uri",
      "gs_uri",
      "音频路径",
      default="",
  )
  gcs_ref_uri = ""
  if raw_audio_url.startswith("gs://") or raw_audio_url.startswith((
      "https://storage.cloud.google.com/",
      "https://storage.googleapis.com/",
  )):
    b_name, obj_path = GCSStorageManager.parse_gs_uri(
        raw_audio_url, default_bucket=GCS_MANAGER.bucket_name
    )
    if b_name and obj_path:
      gcs_ref_uri = f"gs://{b_name}/{obj_path}"
      raw_audio_url = (
          f"/api/gcs/stream?uri={urllib.parse.quote(gcs_ref_uri, safe='')}"
      )

  ref_lyrics = pick(
      "reference_lyrics",
      "ref_lyrics",
      "参考歌词",
      default="",
  )

  return {
      "id": item_id,
      "group": group,
      "seq": int(seq) if str(seq).isdigit() else seq,
      "taskid": pick("taskid", "task_id", default=f"custom_{item_id}"),
      "country": pick("country", "国家/地区", default="Global"),
      "language": language,
      "lyria_official_lang_supported": (
          "-"
          if is_instrumental
          else ("是" if language in LYRIA_SUPPORTED_LANGUAGES else "否")
      ),
      "user_lyrics": user_lyrics,
      "reference_lyrics": ref_lyrics,
      "user_prompt": user_prompt,
      "genre": genre,
      "raw_style": raw_style,
      "vocal_gender": vocal_gender,
      "is_instrumental": is_instrumental,
      "result_audio_url": raw_audio_url,
      "gcs_reference_uri": gcs_ref_uri,
      "requested_duration_param": req_dur_str or "auto",
      "target_duration_sec": target_dur_val,
      "reference_duration_sec": display_ref_dur,
      "actual_duration_sec": display_ref_dur,
      "note": pick("note", "备注", default=""),
  }


def parse_uploaded_prompt_list(
    filename: str, raw_bytes: bytes, target_group: str = ""
) -> list[dict]:
  """Parses an uploaded CSV, XLSX, JSON, or TXT file into normalized prompt items."""
  ext = Path(filename).suffix.lower()
  stem_low = Path(filename).stem.lower()
  tg = (target_group or "").strip().upper()
  if tg in ("A", "B", "C"):
    file_default_group = tg
  elif any(k in stem_low for k in ("a组", "group_a", "a_lyrics", "lyrics_a")) or stem_low.startswith("a_"):
    file_default_group = "A"
  elif any(k in stem_low for k in ("b组", "group_b", "b_prompt", "prompts_b")) or stem_low.startswith("b_"):
    file_default_group = "B"
  elif any(k in stem_low for k in ("c组", "group_c", "c_inst", "instrumental", "纯音乐")) or stem_low.startswith("c_"):
    file_default_group = "C"
  else:
    file_default_group = "U"
  items = []

  if ext == ".json":
    data = json.loads(raw_bytes.decode("utf-8-sig"))
    raw_list = data.get("items", data) if isinstance(data, dict) else data
    if not isinstance(raw_list, list):
      raise ValueError("JSON prompt list must be an array or contain 'items'")
    for idx, r in enumerate(raw_list, start=1):
      if isinstance(r, str):
        r = {"prompt": r}
      items.append(
          normalize_prompt_item(r, idx, default_group=file_default_group)
      )

  elif ext in (".csv", ".tsv"):
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    delim = "\t" if ext == ".tsv" else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    fieldnames = [f for f in (reader.fieldnames or []) if f]
    for idx, row in enumerate(reader, start=1):
      non_none_keys = [k for k, v in row.items() if k is not None and v is not None and str(v).strip() != ""]
      if not non_none_keys:
        continue
      # Handle unquoted multiline lyrics/prompts gracefully if only the first column received text
      if (
          len(fieldnames) >= 2
          and len(non_none_keys) == 1
          and non_none_keys[0] == fieldnames[0]
          and row.get(fieldnames[1]) is None
          and items
      ):
        cont_line = str(row[fieldnames[0]]).strip()
        prev = items[-1]
        if prev.get("user_lyrics"):
          prev["user_lyrics"] = f"{prev['user_lyrics']}\n{cont_line}"
        else:
          prev["user_prompt"] = f"{prev.get('user_prompt', '')}\n{cont_line}".strip()
        continue
      it = normalize_prompt_item(row, len(items) + 1, default_group=file_default_group)
      if it.get("user_prompt") or it.get("user_lyrics") or it.get("result_audio_url"):
        items.append(it)

  elif ext == ".xlsx":
    sheets = parse_xlsx_bytes(raw_bytes)
    idx = 1
    for s_name, rows in sheets.items():
      if s_name in ("说明", "分布统计") or len(rows) < 2:
        continue
      headers = [h.strip() for h in rows[0]]
      grp = (
          tg
          if tg in ("A", "B", "C")
          else (
              "A"
              if s_name.upper().startswith("A")
              else ("B" if s_name.upper().startswith("B") else ("C" if s_name.upper().startswith("C") else file_default_group))
          )
      )
      for r in rows[1:]:
        row_dict = {
            headers[i]: r[i]
            for i in range(min(len(headers), len(r)))
            if headers[i]
        }
        if any(row_dict.values()):
          items.append(normalize_prompt_item(row_dict, idx, default_group=grp))
          idx += 1

  else:
    # Plain text (.txt / .md): one prompt per non-empty line
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    for idx, line in enumerate(text.splitlines(), start=1):
      line = line.strip()
      if line and not line.startswith("#"):
        items.append(
            normalize_prompt_item(
                {"prompt": line}, idx, default_group=file_default_group
            )
        )

  # Enforce explicit target_group if specified by user ("A", "B", or "C")
  if tg in ("A", "B", "C"):
    for idx, it in enumerate(items, start=1):
      it["group"] = tg
      if tg == "C":
        it["is_instrumental"] = 1
        it["lyria_official_lang_supported"] = "-"
      raw_id = str(it.get("id") or "")
      if not raw_id or raw_id.startswith("U"):
        seq_val = it.get("seq") or idx
        it["id"] = f"{tg}{int(seq_val):02d}" if str(seq_val).isdigit() else f"{tg}_{idx:02d}"

  return items


def _apply_items_to_builtin_dataset(
    items: list[dict],
    source_label: str,
    audio_assets: list[dict] | None = None,
    merge_existing: bool = False,
) -> dict:
  """Populates or merges `BUILTIN_DATASET` from a list of normalized items (e.g. uploaded or loaded from GCS)

  and automatically classifies generic `U` items into Group A / B / C, binds matching GCS audio,
  and dynamically rebuilds specification & distribution statistics tables.
  """
  group_seq_counters = {"A": 0, "B": 0, "C": 0}
  for it in items:
    if it.get("group") not in ("A", "B", "C"):
      if it.get("is_instrumental") == 1:
        it["group"] = "C"
      elif (it.get("user_lyrics") or "").strip():
        it["group"] = "A"
      else:
        it["group"] = "B"
    grp = it["group"]
    group_seq_counters[grp] = group_seq_counters.get(grp, 0) + 1
    raw_id = str(it.get("id") or "").strip()
    if not raw_id or raw_id.startswith("U"):
      seq_val = it.get("seq")
      n = int(seq_val) if str(seq_val).isdigit() else group_seq_counters[grp]
      it["id"] = f"{grp}{n:02d}"

  if merge_existing and BUILTIN_DATASET.get("items"):
    merged_map: dict[str, dict] = {}
    for old_it in BUILTIN_DATASET.get("items", []):
      oid = str(old_it.get("id") or "")
      if oid:
        merged_map[oid] = old_it
    for new_it in items:
      nid = str(new_it.get("id") or "")
      if nid:
        merged_map[nid] = new_it
    group_order = {"A": 0, "B": 1, "C": 2}
    items = sorted(
        merged_map.values(),
        key=lambda x: (group_order.get(x.get("group", "A"), 9), str(x.get("id", ""))),
    )

  if audio_assets:
    by_guess = {}
    for a in audio_assets:
      guess = (a.get("item_id_guess") or "").upper()
      if guess and guess not in by_guess:
        by_guess[guess] = a
    for it in items:
      for k in (
          str(it.get("id") or "").upper(),
          str(it.get("seq") or "").upper(),
          str(it.get("taskid") or "").upper(),
      ):
        if k and k in by_guess:
          it["result_audio_url"] = by_guess[k]["stream_url"]
          it["gcs_reference_uri"] = by_guess[k]["gs_uri"]
          break

  items_a = [x for x in items if x.get("group") == "A"]
  items_b = [x for x in items if x.get("group") == "B"]
  items_c = [x for x in items if x.get("group") == "C"]
  cnt_a, cnt_b, cnt_c = len(items_a), len(items_b), len(items_c)

  BUILTIN_DATASET["items"] = items
  BUILTIN_DATASET["total_count"] = len(items)
  BUILTIN_DATASET["source_folder"] = source_label
  BUILTIN_DATASET["counts"] = {
      "group_a_lyrics": cnt_a,
      "group_b_prompts": cnt_b,
      "group_c_instrumental": cnt_c,
  }
  BUILTIN_DATASET["groups"] = {"A": items_a, "B": items_b, "C": items_c}

  langs = sorted({x.get("language", "English") for x in items if x.get("language")})
  genres = sorted({x.get("genre", "Pop") for x in items if x.get("genre")})
  countries = sorted({x.get("country", "Global") for x in items if x.get("country")})
  BUILTIN_DATASET["filters"] = {
      "genres": genres,
      "languages": langs,
      "countries": countries,
      "lyria_supported_languages": LYRIA_SUPPORTED_LANGUAGES,
  }

  BUILTIN_DATASET["files_analyzed"] = [
      {
          "filename": "A组 · 用户歌词成曲 (Group A)",
          "sample_count": cnt_a,
          "tab_id": "tab-group-a",
          "tab_title": "🎤 A组 · 用户歌词成曲",
          "description": "包含用户完整结构化歌词与风格描述，锁定原词 (is_user_provided=true) 编译多段式英文配器与人声方案。",
      },
      {
          "filename": "B组 · 提示词写词成曲 (Group B)",
          "sample_count": cnt_b,
          "tab_id": "tab-group-b",
          "tab_title": "✨ B组 · 提示词写词成曲",
          "description": "根据简短风格/主题提示词自动创作目标语种押韵歌词与多段式声学描述。",
      },
      {
          "filename": "C组 · 纯音乐与配乐 (Group C)",
          "sample_count": cnt_c,
          "tab_id": "tab-group-c",
          "tab_title": "🎹 C组 · 纯音乐与配乐",
          "description": "纯音乐器乐模式 (is_instrumental=1)，将场景提示词编译为多段动态起伏器乐编曲。",
      },
  ]

  # Dynamically build spec_table & stats_table so Tab 4 overview tables always reflect loaded data
  BUILTIN_DATASET["spec_table"] = [
      ["组别 / 维度", "样本数量", "模式特征", "数据来源"],
      ["A组 · 用户歌词成曲", f"{cnt_a} 条", "非纯音乐 (is_instrumental=0) · 锁定用户歌词", source_label],
      ["B组 · 提示词写词成曲", f"{cnt_b} 条", "非纯音乐 (is_instrumental=0) · 自动创作目标语种歌词", source_label],
      ["C组 · 纯音乐与配乐", f"{cnt_c} 条", "纯音乐 (is_instrumental=1) · 纯器乐多段动态编曲", source_label],
      ["总计 (Total)", f"{len(items)} 条", f"覆盖 {len(langs)} 种语言 × {len(genres)} 类流派", source_label],
  ]

  top_genres = genres[:8] if genres else ["Pop"]
  stats_rows = [["语言 \\ 流派", *top_genres, "合计"]]
  for lg in langs:
    lg_items = [x for x in items if x.get("language") == lg]
    row_counts = [
        str(sum(1 for x in lg_items if x.get("genre") == g)) for g in top_genres
    ]
    stats_rows.append([lg, *row_counts, str(len(lg_items))])
  BUILTIN_DATASET["stats_table"] = stats_rows

  return BUILTIN_DATASET


def load_builtin_dataset() -> dict:
  if DATASET_JSON_PATH.exists():
    try:
      return json.loads(DATASET_JSON_PATH.read_text(encoding="utf-8"))
    except Exception as e:
      print(f"[Dataset] Error loading {DATASET_JSON_PATH}: {e}")
  return {
      "version": "text2music_eval_set_v2",
      "total_count": 0,
      "counts": {"group_a_lyrics": 0, "group_b_prompts": 0, "group_c_instrumental": 0},
      "files_analyzed": [],
      "filters": {"genres": [], "languages": [], "countries": [], "lyria_supported_languages": LYRIA_SUPPORTED_LANGUAGES},
      "spec_table": [],
      "stats_table": [],
      "groups": {"A": [], "B": [], "C": []},
      "items": [],
  }


BUILTIN_DATASET = load_builtin_dataset()


# ==============================================================================
# 4. Audio Probing, External URL Fetching & Loudness Normalization Helpers
# ==============================================================================
def probe_audio_file(path: Path) -> dict:
  """Extracts duration, sample_rate, channels, codec, and 60-bin waveform peak envelope."""
  info = {
      "duration_sec": 0.0,
      "sample_rate": 48000,
      "channels": 2,
      "codec": path.suffix.lstrip(".") or "mp3",
      "size_bytes": path.stat().st_size if path.exists() else 0,
      "waveform_peaks": [],
  }
  if not path.exists():
    return info
  try:
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if res.returncode == 0:
      data = json.loads(res.stdout)
      fmt = data.get("format", {})
      info["duration_sec"] = round(float(fmt.get("duration", 0.0)), 2)
      for st in data.get("streams", []):
        if st.get("codec_type") == "audio":
          info["sample_rate"] = int(st.get("sample_rate", 48000))
          info["channels"] = int(st.get("channels", 2))
          info["codec"] = st.get("codec_name", info["codec"])
          break
  except Exception:
    pass
  return info


def normalize_audio_minus14_lufs(src_path: Path, dst_path: Path) -> Path:
  """Applies Vertex Lyria standard -14.0 LUFS loudness normalization via ffmpeg."""
  try:
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src_path),
        "-af",
        "loudnorm=I=-14:TP=-1.0:LRA=11",
        "-ar",
        "44100",
        "-b:a",
        "192k",
        str(dst_path),
    ]
    res = subprocess.run(cmd, capture_output=True, timeout=60)
    if res.returncode == 0 and dst_path.exists() and dst_path.stat().st_size > 1000:
      return dst_path
  except Exception as e:
    print(f"[Audio] Loudnorm skipped: {e}")
  if src_path != dst_path:
    shutil.copy2(src_path, dst_path)
  return dst_path


def probe_tail_rms_db(path: Path, tail_window_sec: float = 0.6) -> float:
  """Measures the RMS level (in dB) of the final `tail_window_sec` of an audio file.

  Returns a negative dB value (e.g. -14.2 for loud abrupt cutoff, -55.0 for natural silence).
  """
  if not path.exists():
    return -90.0
  try:
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-sseof",
        f"-{max(0.2, float(tail_window_sec)):.2f}",
        "-i",
        str(path),
        "-af",
        "astats=metadata=1:reset=0",
        "-f",
        "null",
        "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    matches = re.findall(r"RMS level dB:\s*([-\d\.]+|inf|-inf)", res.stderr or "")
    numeric_vals = []
    for m in matches:
      if m not in ("inf", "-inf", "-"):
        try:
          numeric_vals.append(float(m))
        except ValueError:
          pass
    if numeric_vals:
      return round(max(numeric_vals), 2)
  except Exception:
    pass
  return -60.0


def apply_universal_audio_postprocess(
    src_path: Path, dst_path: Path, cfg: dict
) -> dict:
  """Universal audio post-processor:

  1. Vertex `lyria-3-*` natively outputs `-14.0 dB` normalized 192kbps MP3.
     By default (`auto_loudnorm_minus14_lufs=False`), we preserve the native master without redundant re-encoding.
  2. If `auto_tail_fade_guard=True`, probes the final 0.6s RMS energy. If tail RMS > -26.0 dB
     (indicating a boundary slice while instruments/vocals are still active), applies a smooth
     cosine fade-out (`afade=t=out:curve=qsin`) over the final `tail_fade_duration_sec` (default 1.8s)
     to eliminate hard-cut clicks.
  """
  probe_in = probe_audio_file(src_path)
  dur = float(probe_in.get("duration_sec") or 0.0)
  tail_rms_db = probe_tail_rms_db(src_path, tail_window_sec=0.6)

  auto_tail_guard = bool(cfg.get("auto_tail_fade_guard", True))
  tail_fade_sec = max(0.5, min(5.0, float(cfg.get("tail_fade_duration_sec", 1.8))))
  want_loudnorm = bool(cfg.get("auto_loudnorm_minus14_lufs", False))

  # Trigger tail fade guard only when the final 0.6s still carries significant energy (> -26 dB)
  needs_tail_fade = auto_tail_guard and dur > 6.0 and tail_rms_db > -26.0

  filters = []
  if want_loudnorm:
    filters.append("loudnorm=I=-14:TP=-1.0:LRA=11")
  if needs_tail_fade:
    eff_fade = min(tail_fade_sec, round(dur * 0.12, 2))
    fade_start = max(0.0, round(dur - eff_fade, 2))
    filters.append(f"afade=t=out:st={fade_start:.2f}:d={eff_fade:.2f}:curve=qsin")

  tail_fade_applied = False
  loudnorm_applied = False

  if filters or src_path.suffix.lower() != dst_path.suffix.lower():
    af_chain = ",".join(filters) if filters else "anull"
    try:
      cmd = [
          "ffmpeg",
          "-y",
          "-i",
          str(src_path),
          "-af",
          af_chain,
          "-ar",
          "48000",
          "-b:a",
          "192k",
          str(dst_path),
      ]
      res = subprocess.run(cmd, capture_output=True, timeout=60)
      if res.returncode == 0 and dst_path.exists() and dst_path.stat().st_size > 1000:
        tail_fade_applied = needs_tail_fade
        loudnorm_applied = want_loudnorm
        if src_path != dst_path:
          src_path.unlink(missing_ok=True)
        return {
            "tail_rms_db": tail_rms_db,
            "tail_fade_applied": tail_fade_applied,
            "loudnorm_applied": loudnorm_applied,
        }
    except Exception as e:
      print(f"[AudioPostProcess] ffmpeg post-process skipped: {e}")

  if src_path != dst_path:
    shutil.move(str(src_path), str(dst_path))
  return {
      "tail_rms_db": tail_rms_db,
      "tail_fade_applied": False,
      "loudnorm_applied": False,
  }


def resolve_audio_source_to_local(source_ref: str) -> tuple[Path, str]:
  """Resolves a local workspace ID/path, `/api/audio/...` path, `gs://` URI, or `https://` URL to a local file."""
  s = (source_ref or "").strip()
  if not s:
    raise ValueError("Empty audio source reference")

  # 1. Direct path inside WORKSPACE_DIR
  cand = Path(s)
  if cand.is_file():
    return cand, _guess_audio_mime(cand)

  # 2. Relative to GENERATED_DIR or UPLOADS_AUDIO_DIR
  for folder in [GENERATED_DIR, UPLOADS_AUDIO_DIR]:
    p = folder / Path(s).name
    if p.is_file():
      return p, _guess_audio_mime(p)

  # 3. URL `/api/audio/<folder>/<filename>`
  if "/api/audio/" in s:
    parts = s.split("/api/audio/")[-1].split("?")[0].strip("/").split("/")
    if len(parts) >= 2:
      sub, fname = parts[0], parts[1]
      folder = GENERATED_DIR if sub == "generated" else UPLOADS_AUDIO_DIR
      p = folder / fname
      if not p.is_file():
        rel_gcs = (
            f"generations/{fname}"
            if sub == "generated"
            else f"uploads/audio/{fname}"
        )
        GCS_MANAGER.download_file(rel_gcs, p)
      if p.is_file():
        return p, _guess_audio_mime(p)

  # 3a. URL `/api/gcs/stream?uri=gs://...`
  if "/api/gcs/stream" in s:
    b_name, obj_path = GCSStorageManager.parse_gs_uri(
        s, default_bucket=GCS_MANAGER.bucket_name
    )
    if b_name and obj_path:
      local_p = GCS_MANAGER.download_arbitrary_gs_uri(
          f"gs://{b_name}/{obj_path}"
      )
      return local_p, _guess_audio_mime(local_p)

  # 3b. Direct `gs://<bucket>/<object>` or authenticated GCS storage URL
  if s.startswith("gs://") or s.startswith((
      "https://storage.cloud.google.com/",
      "https://storage.googleapis.com/",
  )):
    b_name, obj_path = GCSStorageManager.parse_gs_uri(
        s, default_bucket=GCS_MANAGER.bucket_name
    )
    if b_name and obj_path:
      try:
        local_p = GCS_MANAGER.download_arbitrary_gs_uri(
            f"gs://{b_name}/{obj_path}"
        )
        return local_p, _guess_audio_mime(local_p)
      except Exception:
        pass
    fname = Path(s.split("?", 1)[0]).name
    for sub_folder, local_folder in [
        ("generations", GENERATED_DIR),
        ("uploads/audio", UPLOADS_AUDIO_DIR),
    ]:
      p = local_folder / fname
      if not p.is_file():
        GCS_MANAGER.download_file(f"{sub_folder}/{fname}", p)
      if p.is_file():
        return p, _guess_audio_mime(p)

  # 4. External HTTP/HTTPS URL (e.g., `结果音频URL` from text2music_120 dataset)
  if s.startswith("http://") or s.startswith("https://"):
    url_hash = hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]
    ext = Path(urllib.parse.urlparse(s).path).suffix.lower() or ".mp3"
    if ext not in (".mp3", ".wav", ".ogg", ".flac", ".m4a"):
      ext = ".mp3"
    cached_path = UPLOADS_AUDIO_DIR / f"remote_{url_hash}{ext}"
    if not cached_path.exists() or cached_path.stat().st_size < 512:
      req = urllib.request.Request(
          s, headers={"User-Agent": "MusicStudio/2.0"}
      )
      with urllib.request.urlopen(req, timeout=45) as resp:
        cached_path.write_bytes(resp.read())
    return cached_path, _guess_audio_mime(cached_path)

  raise FileNotFoundError(f"Could not resolve audio source: {source_ref}")


def _guess_audio_mime(path: Path) -> str:
  ext = path.suffix.lower()
  return {
      ".mp3": "audio/mpeg",
      ".wav": "audio/wav",
      ".ogg": "audio/ogg",
      ".flac": "audio/flac",
      ".m4a": "audio/mp4",
  }.get(ext, "audio/mpeg")


# ==============================================================================
# 5. Gemini (`gemini-4-argon`, `thinking_level="MEDIUM"`) Client & Helper
# ==============================================================================
def get_genai_client(location_override: str | None = None):
  if not HAS_GENAI_SDK:
    raise RuntimeError("google-genai SDK is not installed")
  cfg = CONFIG_STORE.get()
  project_id = cfg.get("project_id") or "your-gcp-project-id"
  location = location_override or cfg.get("location") or "global"
  return genai.Client(vertexai=True, project=project_id, location=location)


def build_thinking_config(thinking_level_str: str | None = None):
  """Builds ThinkingConfig(thinking_level='MEDIUM') for gemini-4-argon."""
  cfg = CONFIG_STORE.get()
  lvl = (thinking_level_str or cfg.get("gemini_thinking_level") or "MEDIUM").upper()
  if lvl in ("NONE", "OFF", "DISABLED"):
    return None
  try:
    return types.ThinkingConfig(thinking_level=lvl)
  except Exception:
    return None


def extract_json_from_text(text: str) -> dict:
  """Robustly parses JSON object from LLM text response."""
  raw = (text or "").strip()
  if not raw:
    return {}
  # Strip markdown code fences if present
  if "```" in raw:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.DOTALL)
    if m:
      raw = m.group(1).strip()
  try:
    return json.loads(raw)
  except Exception:
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
      try:
        return json.loads(raw[start : end + 1])
      except Exception:
        pass
  return {"raw_text": text}


def extract_usage_from_response(
    resp: Any,
    fallback_input_text: str = "",
    fallback_output_text: str = "",
    fallback_audio_sec: float = 0.0,
    include_thinking_est: bool = True,
) -> dict:
  """Extracts token usage metadata from a Vertex AI GenAI response, or estimates deterministically."""
  um = getattr(resp, "usage_metadata", None) if resp is not None else None
  if um is not None:
    p_tok = int(getattr(um, "prompt_token_count", 0) or 0)
    c_tok = int(getattr(um, "candidates_token_count", 0) or 0)
    t_tok = int(getattr(um, "thoughts_token_count", 0) or 0)
    tot_tok = int(getattr(um, "total_token_count", 0) or (p_tok + c_tok + t_tok))
    if p_tok > 0 or c_tok > 0 or tot_tok > 0:
      return {
          "input_tokens": p_tok,
          "output_tokens": c_tok,
          "thinking_tokens": t_tok,
          "total_tokens": tot_tok,
          "audio_input_sec": round(float(fallback_audio_sec or 0.0), 2),
          "source": "vertex_usage_metadata",
      }

  audio_tokens = int(round(float(fallback_audio_sec or 0.0) * 32.0))
  text_in_tokens = int(round(len(fallback_input_text or "") / 3.6))
  in_tok = max(1, audio_tokens + text_in_tokens)
  out_tok = max(1, int(round(len(fallback_output_text or "") / 3.6)))
  think_tok = int(round(out_tok * 0.55)) if include_thinking_est else 0
  return {
      "input_tokens": in_tok,
      "output_tokens": out_tok,
      "thinking_tokens": think_tok,
      "total_tokens": in_tok + out_tok + think_tok,
      "audio_input_sec": round(float(fallback_audio_sec or 0.0), 2),
      "source": "estimated_from_payload",
  }


# ==============================================================================
# 6. Lyria Multi-Section Conditioning & Duration Alignment Engine
# ==============================================================================
VOCAL_GENDER_TRANSLATION_MAP = {
    "lalaki": "Male lead vocalist",
    "babae": "Female lead vocalist",
    "male": "Male lead vocalist",
    "female": "Female lead vocalist",
    "男": "Male lead vocalist",
    "女": "Female lead vocalist",
    "男声": "Male lead vocalist",
    "女声": "Female lead vocalist",
    "男女对唱": "Male and Female vocal duet",
    "duet": "Male and Female vocal duet",
    "hombre": "Male lead vocalist",
    "mujer": "Female lead vocalist",
    "homme": "Male lead vocalist",
    "femme": "Female lead vocalist",
    "pria": "Male lead vocalist",
    "wanita": "Female lead vocalist",
}


class LyriaBestPracticeEngine:
  """Implements Lyria 3 multi-section conditioning, stateless section captioning,

  universal lyric-density pacing, and dedicated instrumental outro anchoring.
  """

  @staticmethod
  def normalize_vocal_gender(raw_gender: str, is_instrumental: bool) -> str:
    if is_instrumental:
      return "none (purely instrumental, no vocals, no singing)"
    g = (raw_gender or "").strip()
    if not g:
      return "Expressive lead vocalist matching genre aesthetic"
    low = g.lower()
    for k, v in VOCAL_GENDER_TRANSLATION_MAP.items():
      if k == low or k in low:
        return v
    return f"{g} lead vocalist"

  @staticmethod
  def clamp_duration_for_model(requested_sec: float, lyria_model: str) -> float:
    """Enforces Lyria 3 model duration bounds (30.0s clip mode vs. 60.0s–180.0s full-song mode)."""
    m = (lyria_model or "").lower()
    if "clip" in m:
      return 30.0
    # Full song (`lyria-3-pro-preview`): must be in [60.0, 180.0]
    if requested_sec <= 0:
      return 145.0
    return max(60.0, min(180.0, round(float(requested_sec), 1)))

  @staticmethod
  def estimate_optimal_duration_sec(
      user_lyrics: str = "",
      user_prompt: str = "",
      genre: str = "Pop",
      raw_style: str = "",
      is_instrumental: bool = False,
      requested_duration_raw: str | float | None = None,
      reference_duration_sec: float = 0.0,
      duration_strategy: str | None = None,
      **kwargs,
  ) -> float:
    return estimate_optimal_song_duration_sec(
        user_lyrics=user_lyrics,
        user_prompt=user_prompt,
        genre=genre,
        raw_style=raw_style,
        is_instrumental=is_instrumental,
        requested_duration_raw=requested_duration_raw,
        reference_duration_sec=reference_duration_sec,
        duration_strategy=duration_strategy,
        **kwargs,
    )

  @staticmethod
  def sanitize_prompt_for_self_healing(text: str) -> str:
    """Sanitizes potential recitation/artist trigger phrases into pure acoustic descriptions for self-healing retries."""
    s = (text or "").strip()
    if not s:
      return s
    # Replace common artist-reference patterns like "in the style of X", "sounds like X"
    s = re.sub(
        r"(?i)\b(?:in the style of|sounding like|sounds like|inspired by|similar to|voice like|vocalist like)\s+[A-Z][\w\s&\-\.]{1,28}",
        "with authentic studio production aesthetic",
        s,
    )
    return s

  @classmethod
  def compute_universal_pacing_profile(
      cls,
      duration_secs: float,
      is_instrumental: bool,
      user_lyrics: str,
      is_clip: bool,
      outro_buffer_sec: float = 8.0,
  ) -> dict:
    """Computes a 100% dataset-independent Universal Pacing & Density Profile based solely on

    (target_duration_secs, is_instrumental, user_lyrics, is_clip).
    Prevents both early cutoff (when lyrics/sections are too sparse) and tail truncation
    (when lyrics are too dense or lack a dedicated `[Outro]` anchor).
    """
    eff_outro_buf = (
        0.0
        if is_clip
        else min(
            max(5.0, float(outro_buffer_sec or 8.0)),
            round(duration_secs * 0.16, 1),
        )
    )
    outro_start_sec = round(max(0.0, duration_secs - eff_outro_buf), 1)

    if is_clip:
      return {
          "input_state": "clip_30s",
          "target_duration_secs": 30.0,
          "outro_buffer_sec": 0.0,
          "outro_start_sec": 30.0,
          "recommended_section_count": 1,
          "pacing_strategy": "single_30s_clip",
          "compiler_directive": (
              "30-second clip mode (`is_clip=True`): compile exactly 1 self-contained section `[Main Clip]` [0.0s - 30.0s]."
          ),
      }

    # State 1: Pure Instrumental (`is_instrumental = True`)
    if is_instrumental:
      rec_sections = max(4, min(8, int(round(duration_secs / 22.0))))
      return {
          "input_state": "instrumental",
          "target_duration_secs": duration_secs,
          "outro_buffer_sec": eff_outro_buf,
          "outro_start_sec": outro_start_sec,
          "recommended_section_count": rec_sections,
          "pacing_strategy": "dynamic_instrumental_movements_with_outro",
          "compiler_directive": (
              f"PURE INSTRUMENTAL PACING: Partition [0.0s - {outro_start_sec:.1f}s] into {rec_sections - 1} "
              "distinct evolving musical movements (~18-24s each: Intro -> Theme A -> Rhythmic Variation -> "
              "Counter-Melody/Bridge -> Climax) with rich instrumentation changes so the arrangement stays dynamic "
              f"across the full duration. Anchor a dedicated pure-instrumental `[Outro]` at "
              f"[{outro_start_sec:.1f}s - {duration_secs:.1f}s] with a resolving cadence and smooth release."
          ),
      }

    clean_lines = [
        ln.strip()
        for ln in (user_lyrics or "").splitlines()
        if ln.strip() and not re.match(r"^\s*[\[\(].*?[\]\)]\s*$", ln.strip())
    ]

    # State 2: Vocal Song without Preset Lyrics (`user_lyrics` is empty)
    if not clean_lines:
      vocal_window = max(24.0, outro_start_sec - 10.0)
      rec_lines = max(8, min(36, int(round(vocal_window / 4.0))))
      rec_sections = max(5, min(8, int(round(duration_secs / 22.0))))
      return {
          "input_state": "vocal_auto_lyrics",
          "target_duration_secs": duration_secs,
          "outro_buffer_sec": eff_outro_buf,
          "outro_start_sec": outro_start_sec,
          "recommended_section_count": rec_sections,
          "recommended_total_lyric_lines": rec_lines,
          "pacing_strategy": "duration_matched_lyric_generation",
          "compiler_directive": (
              f"AUTO-LYRIC VOLUME MATCHING: Compose approximately {rec_lines} singable lyric lines distributed "
              f"across {rec_sections - 1} sections (Intro [0-10s], Verse 1, Pre-Chorus, Chorus, Verse 2, Bridge, Final Chorus) "
              f"spanning [0.0s - {outro_start_sec:.1f}s]. All vocals MUST conclude by {outro_start_sec:.1f}s, followed by a "
              f"dedicated pure-instrumental `[Outro]` (`lyrics: []`) at [{outro_start_sec:.1f}s - {duration_secs:.1f}s] "
              "with resolving chords."
          ),
      }

    # State 3: Vocal Song with User-Provided Lyrics (`user_lyrics` is present)
    total_cjk = 0
    total_words = 0
    est_singing_sec = 0.0
    for ln in clean_lines:
      cjk_chars = len(re.findall(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", ln))
      non_cjk_words = len(
          re.findall(
              r"[A-Za-zÀ-ÖØ-öø-ÿА-Яа-я0-9]+",
              re.sub(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", " ", ln),
          )
      )
      total_cjk += cjk_chars
      total_words += non_cjk_words
      line_sec = max(2.6, min(6.0, cjk_chars * 0.27 + non_cjk_words * 0.40 + 0.85))
      est_singing_sec += line_sec

    # Add natural inter-phrase breathing/instrumental turnaround overhead
    est_total_vocal_span = round(est_singing_sec * 1.15, 1)
    available_vocal_window = max(25.0, outro_start_sec - 8.0)
    density_ratio = round(est_total_vocal_span / available_vocal_window, 2)

    if density_ratio < 0.65:
      pacing_strategy = "expand_instrumental_interludes"
      directive = (
          f"SPARSE USER LYRICS ADAPTATION ({len(clean_lines)} lines, est. {est_total_vocal_span:.0f}s singing vs. "
          f"{duration_secs:.0f}s target): Preserve all user lyrics 100% verbatim (`is_user_provided=true`) and DO NOT "
          "invent extra unauthorized verses. To prevent early termination before the target duration, insert an "
          "expressive `[Instrumental Intro]` (12-18s), a `[Melodic Instrumental Interlude / Solo]` (16-24s) between "
          f"vocal sections, and anchor the dedicated `[Outro]` (`lyrics: []`) at [{outro_start_sec:.1f}s - {duration_secs:.1f}s]."
      )
    elif density_ratio > 0.92 or len(clean_lines) >= 28:
      pacing_strategy = "compact_transitions_and_upbeat_pacing"
      max_last_vocal_start = max(30.0, round(outro_start_sec - 16.0, 1))
      directive = (
          f"HIGH-DENSITY USER LYRICS ADAPTATION ({len(clean_lines)} lines, est. {est_total_vocal_span:.0f}s singing vs. "
          f"{duration_secs:.0f}s target): Preserve all user lyrics verbatim (`is_user_provided=true`), keep `[Intro]` "
          "concise (4.0-6.0s), omit mid-song instrumental breaks, choose a brisk, energetic tempo (122-140 BPM unless "
          f"explicitly slow), ensure the final vocal section starts at or before {max_last_vocal_start:.1f}s so every "
          f"lyric line finishes cleanly by {outro_start_sec:.1f}s, and reserve [{outro_start_sec:.1f}s - {duration_secs:.1f}s] "
          "for a dedicated pure-instrumental `[Outro]` (`lyrics: []`) coda."
      )
    else:
      pacing_strategy = "balanced_vocal_arc"
      directive = (
          f"BALANCED USER LYRICS PACING ({len(clean_lines)} lines, est. {est_total_vocal_span:.0f}s singing for "
          f"{duration_secs:.0f}s target): Preserve user lyrics verbatim (`is_user_provided=true`), pace sections "
          f"naturally across [0.0s - {outro_start_sec:.1f}s], conclude all singing by {outro_start_sec:.1f}s, and end "
          f"with a dedicated pure-instrumental `[Outro]` (`lyrics: []`) at [{outro_start_sec:.1f}s - {duration_secs:.1f}s]."
      )

    return {
        "input_state": "vocal_user_lyrics",
        "target_duration_secs": duration_secs,
        "outro_buffer_sec": eff_outro_buf,
        "outro_start_sec": outro_start_sec,
        "user_lyric_line_count": len(clean_lines),
        "user_lyric_cjk_chars": total_cjk,
        "user_lyric_words": total_words,
        "estimated_singing_sec": est_total_vocal_span,
        "density_ratio": density_ratio,
        "pacing_strategy": pacing_strategy,
        "compiler_directive": directive,
    }

  @classmethod
  def enforce_universal_timeline_and_outro(
      cls,
      plan: dict,
      duration_secs: float,
      is_instrumental: bool,
      is_clip: bool,
      pacing_profile: dict,
      cfg: dict,
  ) -> dict:
    """Deterministic post-processor that guarantees:

    1. Exact `duration_secs` and full-duration boundary alignment.
    2. For full songs (`not is_clip`), a dedicated pure-instrumental `[Outro]` section (`lyrics: []`)
       anchored at `[outro_start_sec, duration_secs]` with explicit resolving cadence instructions.
    3. All preceding sections partition `[0.0, outro_start_sec]` monotonically with sufficient time for the
       final vocal section so lyrics are never cut off mid-sentence.
    4. Any out-of-range lyric line timestamps are cleanly interpolated within their parent section bounds.
    """
    plan["duration_secs"] = float(duration_secs)
    plan["full_duration_lock"] = True
    plan["_universal_pacing_profile"] = pacing_profile

    sections = plan.get("sections") or []
    global_cap = (
        plan.get("global_caption_en")
        or "A studio-mastered track with balanced stereo imaging and rich musical arrangement."
    ).strip()

    if is_clip:
      if not sections:
        sections = [{
            "section_code": "[Main Clip]",
            "section_name": "Main Clip",
            "start_sec": 0.0,
            "end_sec": 30.0,
            "caption_en": global_cap,
            "lyrics": [],
        }]
      else:
        sections = [sections[0]]
        sections[0]["start_sec"] = 0.0
        sections[0]["end_sec"] = 30.0
      plan["sections"] = sections
      return plan

    if not cfg.get("enforce_duration_alignment", True):
      return plan

    outro_buf = float(pacing_profile.get("outro_buffer_sec") or 8.0)
    outro_start = round(max(15.0, duration_secs - outro_buf), 1)

    # Filter out any placeholder lyric lines like "(Instrumental)"
    for sec in sections:
      if is_instrumental:
        sec["lyrics"] = []
      else:
        cleaned_lyr = []
        for lyr in sec.get("lyrics") or []:
          txt = (lyr.get("text") or "").strip()
          if not txt or re.match(
              r"^\s*\(?\s*(?:instrumental|no vocals|outro|intro|music only|fade out)[^)]*\)?\s*$",
              txt,
              flags=re.IGNORECASE,
          ):
            continue
          cleaned_lyr.append(lyr)
        sec["lyrics"] = cleaned_lyr

    # Check if the last section is already a pure-instrumental Outro
    outro_tail_phrase = (
        f"Purely instrumental resolving coda, no vocals, no singing, sustaining full melodic and rhythmic "
        f"arrangement through {max(0.0, duration_secs - 1.5):.0f}s before a final resolving chord and gentle release at {duration_secs:.0f}s."
    )

    if not sections:
      sections = [
          {
              "section_code": "[Intro]",
              "section_name": "Intro",
              "start_sec": 0.0,
              "end_sec": round(outro_start * 0.2, 1),
              "caption_en": f"{global_cap} Purely instrumental opening, no vocals.",
              "lyrics": [],
          },
          {
              "section_code": "[Main Section]",
              "section_name": "Main Section",
              "start_sec": round(outro_start * 0.2, 1),
              "end_sec": outro_start,
              "caption_en": global_cap,
              "lyrics": [],
          },
      ]

    last_sec = sections[-1]
    last_name_low = str(last_sec.get("section_name") or "").lower()
    last_has_lyrics = bool(last_sec.get("lyrics"))

    if (not last_has_lyrics) and len(sections) >= 2 and any(
        k in last_name_low for k in ("outro", "ending", "coda", "fade")
    ):
      outro_sec = last_sec
      body_sections = sections[:-1]
      outro_sec["section_name"] = "Outro"
      outro_sec["lyrics"] = []
      cap_existing = (outro_sec.get("caption_en") or global_cap).strip()
      if "fade-out" not in cap_existing.lower() and "fade out" not in cap_existing.lower():
        outro_sec["caption_en"] = f"{cap_existing} {outro_tail_phrase}"
    else:
      # If the last section combined Chorus + Outro or had lyrics, keep it as the final body section
      # and append a dedicated pure-instrumental `[Outro]` anchored at `outro_start`.
      if "outro" in last_name_low and last_has_lyrics:
        last_sec["section_name"] = (
            re.sub(r"(?i)\s*(?:&|and|/|-)?\s*outro\b", "", str(last_sec.get("section_name") or "")).strip()
            or "Final Chorus"
        )
      body_sections = list(sections)
      outro_sec = {
          "section_code": "[Outro]",
          "section_name": "Outro",
          "start_sec": outro_start,
          "end_sec": duration_secs,
          "caption_en": f"{global_cap} {outro_tail_phrase}",
          "lyrics": [],
      }

    # Partition body_sections cleanly across [0.0, outro_start]
    n_body = len(body_sections)
    if n_body == 1:
      body_sections[0]["start_sec"] = 0.0
      body_sections[0]["end_sec"] = outro_start
    else:
      # Compute desired weights for each body section based on existing duration + lyric count
      weights = []
      for i, sec in enumerate(body_sections):
        raw_s = float(sec.get("start_sec") or 0.0)
        raw_e = float(sec.get("end_sec") or (raw_s + 18.0))
        raw_dur = max(6.0, raw_e - raw_s)
        lyr_cnt = len(sec.get("lyrics") or [])
        # Ensure sections with more lyrics receive enough time (at least ~3.4s per line)
        min_lyric_dur = lyr_cnt * 3.4 if lyr_cnt > 0 else 8.0
        w = max(raw_dur, min_lyric_dur)
        if i == 0 and lyr_cnt == 0 and pacing_profile.get("pacing_strategy") == "compact_transitions_and_upbeat_pacing":
          w = min(w, 6.5)
        weights.append(w)

      # Check if existing boundaries are already monotonic, span [0, outro_start], and give the last vocal section enough room
      existing_ok = True
      if abs(float(body_sections[0].get("start_sec") or 0.0)) > 0.5:
        existing_ok = False
      prev_e = 0.0
      for i, sec in enumerate(body_sections):
        s_val = float(sec.get("start_sec") if sec.get("start_sec") is not None else -1.0)
        e_val = float(sec.get("end_sec") if sec.get("end_sec") is not None else -1.0)
        lyr_cnt = len(sec.get("lyrics") or [])
        min_needed = max(6.0, min(14.0, lyr_cnt * 2.5)) if lyr_cnt > 0 else 4.0
        if s_val < prev_e - 0.5 or (e_val - s_val) < min_needed:
          existing_ok = False
          break
        prev_e = e_val
      if abs(prev_e - outro_start) > 3.5:
        existing_ok = False

      if existing_ok:
        # Snap contiguous boundaries and clamp last body section end to `outro_start`
        body_sections[0]["start_sec"] = 0.0
        for i in range(n_body - 1):
          mid = round(float(body_sections[i + 1].get("start_sec") or body_sections[i].get("end_sec") or 0.0), 1)
          body_sections[i]["end_sec"] = mid
          body_sections[i + 1]["start_sec"] = mid
        body_sections[-1]["end_sec"] = outro_start
      else:
        # Rescale proportionally using weights across [0.0, outro_start]
        total_w = sum(weights) or 1.0
        cursor = 0.0
        for i, sec in enumerate(body_sections):
          sec["start_sec"] = round(cursor, 1)
          if i == n_body - 1:
            sec["end_sec"] = outro_start
          else:
            span = max(5.0, round(outro_start * (weights[i] / total_w), 1))
            # Leave at least 6s per remaining section
            max_allowed = outro_start - (n_body - 1 - i) * 6.0
            cursor = min(max_allowed, cursor + span)
            sec["end_sec"] = round(cursor, 1)

    outro_sec["start_sec"] = outro_start
    outro_sec["end_sec"] = round(duration_secs, 1)
    all_sections = body_sections + [outro_sec]

    # Normalize lyric line timestamps inside each section so line times stay within section bounds
    for sec in all_sections:
      s_t = float(sec.get("start_sec") or 0.0)
      e_t = float(sec.get("end_sec") or duration_secs)
      lyrs = sec.get("lyrics") or []
      if not lyrs:
        continue
      n_l = len(lyrs)
      span = max(1.0, e_t - s_t)
      step = span / n_l
      for idx_l, lyr in enumerate(lyrs):
        ls = lyr.get("start_sec")
        le = lyr.get("end_sec")
        valid_bounds = (
            ls is not None
            and le is not None
            and float(ls) >= s_t - 0.2
            and float(le) <= e_t + 0.2
            and float(le) > float(ls)
        )
        if not valid_bounds:
          lyr["start_sec"] = round(s_t + idx_l * step, 1)
          lyr["end_sec"] = round(min(e_t, s_t + (idx_l + 1) * step), 1)

    plan["sections"] = all_sections
    return plan

  @classmethod
  def compile_song_plan_with_gemini(
      cls,
      mode: str,
      user_prompt: str,
      user_lyrics: str,
      genre: str,
      raw_style: str,
      language: str,
      vocal_gender: str,
      is_instrumental: bool,
      target_duration_sec: float,
      lyria_model: str,
      custom_system_prompt: str | None = None,
  ) -> dict:
    """Uses `gemini-4-argon` (`thinking_level="MEDIUM"`) to compile a Lyria SongPlan & Multi-Caption payload."""
    cfg = CONFIG_STORE.get()
    duration_secs = cls.clamp_duration_for_model(target_duration_sec, lyria_model)
    norm_vocal = cls.normalize_vocal_gender(vocal_gender, is_instrumental)
    is_clip = "clip" in (lyria_model or "").lower()
    outro_buffer_sec = float(cfg.get("default_outro_buffer_sec", 12.0))
    pacing_profile = cls.compute_universal_pacing_profile(
        duration_secs=duration_secs,
        is_instrumental=is_instrumental,
        user_lyrics=user_lyrics,
        is_clip=is_clip,
        outro_buffer_sec=outro_buffer_sec,
    )

    sys_instruction = (
        custom_system_prompt
        or cfg.get("prompts", {}).get("lyria_compiler_system_prompt")
        or DEFAULT_LYRIA_COMPILER_SYSTEM_PROMPT
    )

    user_task_payload = {
        "mode": mode,
        "target_lyria_model": lyria_model,
        "is_30s_clip_mode": is_clip,
        "target_duration_secs": duration_secs,
        "requested_raw_duration_secs": target_duration_sec,
        "is_instrumental": bool(is_instrumental),
        "target_sung_language": "Instrumental" if is_instrumental else (language or "English"),
        "normalized_vocal_gender_en": norm_vocal,
        "raw_vocal_gender_tag": vocal_gender,
        "genre_normalized": genre or "Pop",
        "raw_style_tags": raw_style or genre,
        "user_prompt_raw": user_prompt or "",
        "user_lyrics_raw": "" if is_instrumental else (user_lyrics or ""),
        "universal_pacing_profile": pacing_profile,
        "required_json_schema": {
            "song_title": "string",
            "bpm": "number (60 to 180)",
            "duration_secs": duration_secs,
            "global_caption_en": "Rich 45-word English master production overview",
            "vocals_caption_en": (
                "Detailed English vocal timbre/gender/delivery description (or 'none (purely instrumental)' if is_instrumental)"
            ),
            "recitation_safety_notes": (
                "Any artist names or copyright-sensitive phrases sanitized into pure acoustic descriptions"
            ),
            "sections": [
                {
                    "section_code": "[Intro]",
                    "section_name": "Intro",
                    "start_sec": 0.0,
                    "end_sec": 12.0,
                    "caption_en": (
                        "Self-contained 40-60 word English musical arrangement paragraph (stating core genre, BPM feel, active instruments, mix aesthetic, and vocal delivery)"
                    ),
                    "lyrics": [
                        {
                            "start_sec": 0.0,
                            "end_sec": 12.0,
                            "text": "Sung line in target_sung_language (leave `lyrics: []` for instrumental sections or Intro/Outro)",
                            "is_user_provided": bool(user_lyrics and not is_instrumental),
                        }
                    ],
                },
                {
                    "section_code": "[Outro]",
                    "section_name": "Outro",
                    "start_sec": pacing_profile.get("outro_start_sec", max(0.0, duration_secs - 12.0)),
                    "end_sec": duration_secs,
                    "caption_en": (
                        "Dedicated pure-instrumental Outro section with resolving harmonic cadence and smooth release (no vocals, no singing)"
                    ),
                    "lyrics": [],
                },
            ],
        },
    }

    prompt_text = (
        "Compile the following music request into an optimal Google Lyria 3 "
        "Multi-Caption & SongPlan JSON object.\n\n"
        f"INPUT SPECIFICATION:\n{json.dumps(user_task_payload, ensure_ascii=False, indent=2)}"
    )

    t0 = time.time()
    gemini_model = cfg.get("gemini_model", "gemini-4-argon")
    thinking_lvl = cfg.get("gemini_thinking_level", "MEDIUM")
    log_runtime(
        "INFO",
        "GEMINI_COMPILER_START",
        f"Starting SongPlan compilation via {gemini_model} (thinking={thinking_lvl}, target_dur={duration_secs}s, mode={mode})",
        extra={
            "gemini_model": gemini_model,
            "thinking_level": thinking_lvl,
            "mode": mode,
            "genre": genre,
            "language": language,
            "is_instrumental": bool(is_instrumental),
            "target_duration_secs": duration_secs,
            "user_prompt_len": len(user_prompt or ""),
            "user_lyrics_len": len(user_lyrics or ""),
        },
    )
    try:
      client = get_genai_client()
      gen_cfg_kwargs = {
          "system_instruction": sys_instruction,
          "temperature": float(cfg.get("gemini_temperature", 0.4)),
          "response_mime_type": "application/json",
      }
      t_cfg = build_thinking_config(thinking_lvl)
      if t_cfg is not None:
        gen_cfg_kwargs["thinking_config"] = t_cfg

      resp = client.models.generate_content(
          model=gemini_model,
          contents=prompt_text,
          config=types.GenerateContentConfig(**gen_cfg_kwargs),
      )
      plan = extract_json_from_text(resp.text or "")
      comp_lat = round(time.time() - t0, 2)
      plan["_compiler_model"] = gemini_model
      plan["_compiler_thinking_level"] = thinking_lvl
      plan["_compiler_latency_sec"] = comp_lat
      plan["_compiler_usage"] = extract_usage_from_response(
          resp,
          fallback_input_text=(sys_instruction or "") + "\n" + prompt_text,
          fallback_output_text=resp.text or "",
          include_thinking_est=True,
      )
      log_runtime(
          "INFO",
          "GEMINI_COMPILER_DONE",
          f"SongPlan compiled in {comp_lat}s: title='{plan.get('song_title', '')}', sections={len(plan.get('sections') or [])}, bpm={plan.get('bpm')}",
          extra={
              "gemini_model": gemini_model,
              "latency_sec": comp_lat,
              "song_title": plan.get("song_title", ""),
              "bpm": plan.get("bpm"),
              "sections_count": len(plan.get("sections") or []),
              "duration_secs": duration_secs,
          },
      )
    except Exception as e:
      comp_lat = round(time.time() - t0, 2)
      log_runtime(
          "WARNING",
          "GEMINI_COMPILER_FALLBACK",
          f"Gemini compiler failed after {comp_lat}s ({type(e).__name__}: {e}); switching to deterministic multi-section SongPlan",
          extra={"gemini_model": gemini_model, "latency_sec": comp_lat},
          exc=e,
      )
      plan = cls._deterministic_fallback_plan(
          user_prompt=user_prompt,
          user_lyrics=user_lyrics,
          genre=genre,
          raw_style=raw_style,
          language=language,
          norm_vocal=norm_vocal,
          is_instrumental=is_instrumental,
          duration_secs=duration_secs,
          is_clip=is_clip,
          pacing_profile=pacing_profile,
          error_msg=str(e),
      )

    # Enforce universal timeline alignment & dedicated `[Outro]` anchor
    plan = cls.enforce_universal_timeline_and_outro(
        plan=plan,
        duration_secs=duration_secs,
        is_instrumental=is_instrumental,
        is_clip=is_clip,
        pacing_profile=pacing_profile,
        cfg=cfg,
    )

    # Transpile into structured Lyria 3 multi-section prompt + timed_transcript AND SongPlan Markdown
    transpiled = cls.transpile_plan_to_lyria_formats(
        plan=plan,
        is_instrumental=is_instrumental,
        is_clip=is_clip,
        duration_secs=duration_secs,
    )
    plan.update(transpiled)
    return plan

  @classmethod
  def _deterministic_fallback_plan(
      cls,
      user_prompt: str,
      user_lyrics: str,
      genre: str,
      raw_style: str,
      language: str,
      norm_vocal: str,
      is_instrumental: bool,
      duration_secs: float,
      is_clip: bool,
      pacing_profile: dict | None = None,
      error_msg: str = "",
  ) -> dict:
    """Deterministic fallback SongPlan builder if Gemini API is unreachable."""
    base_style = f"{genre or 'Pop'} ({raw_style or 'modern studio production'})"
    vocal_desc = (
        "purely instrumental arrangement, no vocals, no singing, no spoken word"
        if is_instrumental
        else f"{norm_vocal} singing in {language or 'English'}"
    )
    base_caption = (
        f"A studio-quality {base_style} track with cohesive rhythmic groove, "
        f"detailed acoustic and electronic instrumentation, balanced wide stereo mix, "
        f"and {vocal_desc}. {user_prompt or ''}".strip()
    )
    lines = [
        ln.strip()
        for ln in (user_lyrics or "").splitlines()
        if ln.strip() and not re.match(r"^\[.*\]$", ln.strip())
    ]
    outro_start = (
        float(pacing_profile.get("outro_start_sec"))
        if pacing_profile and pacing_profile.get("outro_start_sec")
        else round(max(15.0, duration_secs - 12.0), 1)
    )
    if is_clip:
      sections = [{
          "section_code": "[Main Clip]",
          "section_name": "Main Clip",
          "start_sec": 0.0,
          "end_sec": 30.0,
          "caption_en": base_caption,
          "lyrics": (
              []
              if is_instrumental
              else [{"start_sec": 0.0, "end_sec": 30.0, "text": " / ".join(lines[:6]), "is_user_provided": True}]
          ),
      }]
    else:
      sec_defs = [
          ("[Intro]", "Intro", 0.0, round(outro_start * 0.12, 1)),
          ("[Verse 1]", "Verse 1", round(outro_start * 0.12, 1), round(outro_start * 0.36, 1)),
          ("[Chorus]", "Chorus", round(outro_start * 0.36, 1), round(outro_start * 0.60, 1)),
          ("[Verse 2]", "Verse 2", round(outro_start * 0.60, 1), round(outro_start * 0.80, 1)),
          ("[Final Chorus]", "Final Chorus", round(outro_start * 0.80, 1), outro_start),
          ("[Outro]", "Outro", outro_start, duration_secs),
      ]
      chunk_size = max(1, (len(lines) + 3) // 4) if lines else 1
      sections = []
      for i, (code, name, s_t, e_t) in enumerate(sec_defs):
        sec_lyrics = []
        if not is_instrumental and 1 <= i <= 4 and lines:
          sub = lines[(i - 1) * chunk_size : i * chunk_size] if i < 4 else lines[(i - 1) * chunk_size :]
          for ln in sub:
            sec_lyrics.append({
                "start_sec": None,
                "end_sec": None,
                "text": ln,
                "is_user_provided": True,
            })
        cap = (
            f"Outro section of {base_caption} Purely instrumental resolving cadence and smooth release."
            if name == "Outro"
            else f"{name} section of {base_caption}"
        )
        sections.append({
            "section_code": code,
            "section_name": name,
            "start_sec": s_t,
            "end_sec": e_t,
            "caption_en": cap,
            "lyrics": sec_lyrics,
        })
    return {
        "song_title": f"{genre or 'Studio'} Composition",
        "bpm": 124.0 if len(lines) >= 26 else 120.0,
        "duration_secs": duration_secs,
        "global_caption_en": base_caption,
        "vocals_caption_en": vocal_desc,
        "recitation_safety_notes": f"Fallback planner used ({error_msg})",
        "sections": sections,
    }

  @classmethod
  def transpile_plan_to_lyria_formats(
      cls,
      plan: dict,
      is_instrumental: bool,
      is_clip: bool,
      duration_secs: float,
  ) -> dict:
    """Produces structured arrangement & prompt formats for Lyria 3:

    1. `lyria3_transpiled_prompt`: Multi-section arrangement overview (`[Intro]`, `[Verse 1]`, ...)
    2. `lyria3_timed_transcript`: Time-aligned lyric overview (`[start:end] lyric...`)
    3. `lyria3_song_plan_markdown`: Structured `SongPlan` Markdown (`# Title\n> caption\n## Section [start-end]...`)
    4. `vertex_engineered_prompt`: Unified natural-language prompt for `generate_content(model=lyria-3-pro-preview)`
    """
    bpm = float(plan.get("bpm") or 120.0)
    dur = float(plan.get("duration_secs") or duration_secs)
    vocals_cap = plan.get("vocals_caption_en") or ""
    global_cap = plan.get("global_caption_en") or ""
    title = plan.get("song_title") or "Untitled Studio Track"
    sections = plan.get("sections") or []

    # Normalize section tags to clean readable bracket labels (`[Intro]`, `[Verse 1]`, ...)
    for idx, sec in enumerate(sections):
      sec_name = str(sec.get("section_name") or sec.get("section_code") or f"Section {idx + 1}").strip()
      sec_name = re.sub(r"^\[\[|\]\]$|^\[|\]$", "", sec_name).strip() or f"Section {idx + 1}"
      sec["section_name"] = sec_name
      sec["section_code"] = f"[{sec_name}]"

    if is_clip and sections:
      # 30-second single-caption clip mode
      sec0 = sections[0]
      lyria3_prompt = (
          f"Style & Arrangement: {sec0.get('caption_en', global_cap)}\n"
          f"Tempo: {bpm:.0f} BPM (30s Clip)"
      )
      t_lines = []
      for lyr in sec0.get("lyrics") or []:
        txt = (lyr.get("text") or "").strip()
        if txt:
          s_t = lyr.get("start_sec")
          e_t = lyr.get("end_sec")
          s_str = f"{float(s_t):.1f}" if s_t is not None else ""
          e_str = f"{float(e_t):.1f}" if e_t is not None else ""
          t_lines.append(f"[{s_str}s-{e_str}s] {txt}")
      lyria3_transcript = "\n".join(t_lines)
    else:
      # Multi-section full-song arrangement overview
      p_lines = [
          f"Tempo: {bpm:.0f} BPM | Target Duration: {dur:.1f}s",
      ]
      if vocals_cap and not is_instrumental:
        p_lines.append(f"Vocals: {vocals_cap}")
      t_lines = []
      for sec in sections:
        code = sec["section_code"]
        s_t = float(sec.get("start_sec") or 0.0)
        e_t = float(sec.get("end_sec") or dur)
        cap = (sec.get("caption_en") or global_cap).strip()
        p_lines.append(f"{code} ({s_t:.1f}s-{e_t:.1f}s): {cap}")

        t_lines.append(f"{code} ({s_t:.1f}s-{e_t:.1f}s)")
        sec_lyrics = sec.get("lyrics") or []
        if not is_instrumental and sec_lyrics:
          for lyr in sec_lyrics:
            txt = (lyr.get("text") or "").strip()
            if not txt:
              continue
            ls = lyr.get("start_sec")
            le = lyr.get("end_sec")
            ls_str = f"{float(ls):.1f}" if ls is not None else ""
            le_str = f"{float(le):.1f}" if le is not None else ""
            t_lines.append(f"[{ls_str}s-{le_str}s] {txt}")

      lyria3_prompt = "\n".join(p_lines)
      lyria3_transcript = "" if is_instrumental else "\n".join(t_lines)

    # Structured SongPlan Markdown
    def fmt_mmss(sec_val: float) -> str:
      m = int(sec_val // 60)
      s = sec_val - m * 60
      return f"{m}:{s:04.1f}"

    md_lines = [
        f"# {title}",
        f"> {global_cap}",
        f"> duration: {int(round(dur))}",
        f"> bpm: {int(round(bpm))}",
        f"> vocals: {vocals_cap}",
        "",
    ]
    for sec in sections:
      s_t = float(sec.get("start_sec") or 0.0)
      e_t = float(sec.get("end_sec") or dur)
      name = sec.get("section_name") or sec["section_code"]
      md_lines.append(f"## {name} [{fmt_mmss(s_t)}-{fmt_mmss(e_t)}]")
      md_lines.append(f"> {sec.get('caption_en', global_cap)}")
      if not is_instrumental:
        for lyr in sec.get("lyrics") or []:
          txt = (lyr.get("text") or "").strip()
          if txt:
            md_lines.append(txt)
      md_lines.append("")
    lyria3_markdown = "\n".join(md_lines).strip()

    # Build canonical top-level `sections` + top-level time-aligned `lyrics` specification
    outro_start_anchor = (
        float(sections[-1].get("start_sec") or max(0.0, dur - 8.0))
        if sections
        else max(0.0, dur - 8.0)
    )
    music_gen_sections = []
    top_level_lyrics = []
    for sec in sections:
      s_t = round(float(sec.get("start_sec") or 0.0), 1)
      e_t = round(float(sec.get("end_sec") or dur), 1)
      sec_lyrs_raw = sec.get("lyrics") or []
      if not is_instrumental and sec_lyrs_raw:
        n_l = len(sec_lyrs_raw)
        step = max(1.0, (e_t - s_t)) / max(1, n_l)
        for idx_l, lyr in enumerate(sec_lyrs_raw):
          txt = (lyr.get("text") or "").strip()
          if not txt:
            continue
          ls = lyr.get("start_sec")
          le = lyr.get("end_sec")
          ls_val = round(float(ls), 1) if ls is not None else round(s_t + idx_l * step, 1)
          le_val = round(float(le), 1) if le is not None else round(min(e_t, ls_val + step), 1)
          top_level_lyrics.append({
              "start": ls_val,
              "end": le_val,
              "text": txt,
              "is_user_provided": bool(lyr.get("is_user_provided", True)),
          })
      music_gen_sections.append({
          "section_code": sec["section_code"],
          "section_name": sec.get("section_name") or "Section",
          "caption": {
              "start": int(round(s_t)) if abs(s_t - round(s_t)) < 0.05 else s_t,
              "text": (sec.get("caption_en") or global_cap).strip(),
          },
      })

    music_gen_spec = {
        "duration_secs": int(round(dur)),
        "bpm": int(round(bpm)),
        "vocals_caption": "none" if is_instrumental else vocals_cap,
        "sections": music_gen_sections,
        "lyrics": [] if is_instrumental else top_level_lyrics,
    }

    # Build Stage 1 (`vertex_engineered_prompt`), Stage 2 (`self_healing_stage2_prompt`),
    # and Stage 3 (`self_healing_stage3_prompt`) in clean natural-language multi-section format.
    vertex_lines = [
        f"Create a complete, studio-mastered {int(round(dur))}-second song ({bpm:.0f} BPM, duration_secs={int(round(dur))}).",
        f"Style & Production Direction: {global_cap}",
        (
            "Vocals: Purely instrumental arrangement only. Do NOT include any vocals, singing, humming, or spoken word."
            if is_instrumental
            else f"Vocals: {vocals_cap}. Sing all provided lyrics clearly with natural phrasing and expressive emotion."
        ),
        (
            f"Pacing & Ending Structure: Pace all musical sections across 0s to {dur:.0f}s. "
            + (
                f"Conclude all vocals by {outro_start_anchor:.0f}s, followed by a dedicated instrumental Outro from {outro_start_anchor:.0f}s to {dur:.0f}s with a resolving cadence and smooth fade-out to silence."
                if not is_instrumental
                else f"Conclude with a dedicated resolving Outro from {outro_start_anchor:.0f}s to {dur:.0f}s with a gradual decrescendo and smooth fade-out to silence."
            )
        ),
        "\nSection Progression & Arrangement:",
    ]
    for sec in sections:
      s_t = float(sec.get("start_sec") or 0.0)
      e_t = float(sec.get("end_sec") or dur)
      sec_name = sec.get("section_name", "Section")
      sec_cap = (sec.get("caption_en") or global_cap).strip()
      vertex_lines.append(f"- [{sec_name} {s_t:.0f}s-{e_t:.0f}s]: {sec_cap}")

    if not is_instrumental and top_level_lyrics:
      vertex_lines.append("\nSection Timeline & Lyrics (sing all vocal lines verbatim):")
      for sec in sections:
        s_t = float(sec.get("start_sec") or 0.0)
        e_t = float(sec.get("end_sec") or dur)
        sec_name = sec.get("section_name", "Verse")
        sec_lyrs = [
            (l.get("text") or "").strip()
            for l in (sec.get("lyrics") or [])
            if (l.get("text") or "").strip()
        ]
        if sec_lyrs:
          vertex_lines.append(f"\n[{sec_name} {s_t:.0f}s-{e_t:.0f}s]")
          vertex_lines.extend(sec_lyrs)
        else:
          vertex_lines.append(
              f"\n[{sec_name} {s_t:.0f}s-{e_t:.0f}s] (Instrumental section sustaining full musical energy through {e_t:.0f}s)"
          )

    # Stage 2: Compact sanitized multi-section plan (Proven high-success format without per-section caption repetition)
    stage2_lines = [
        f"Style & Production: {cls.sanitize_prompt_for_self_healing(global_cap)}",
        f"Tempo: {bpm:.0f} BPM. Total Duration: {int(round(dur))} seconds (duration_secs={int(round(dur))}, sustaining full arrangement until {max(0.0, dur - 1.5):.0f}s with a resolving instrumental coda from {outro_start_anchor:.0f}s to {dur:.0f}s).",
        f"Vocals: {'Purely instrumental, no vocals.' if is_instrumental else cls.sanitize_prompt_for_self_healing(vocals_cap)}",
    ]
    if not is_instrumental and top_level_lyrics:
      stage2_lines.append("\nSection Timeline & Lyrics (sing verbatim with expressive vocal delivery):")
      for sec in sections:
        s_t = float(sec.get("start_sec") or 0.0)
        e_t = float(sec.get("end_sec") or dur)
        sec_name = sec.get("section_name", "Verse")
        sec_lyrs = [
            (l.get("text") or "").strip()
            for l in (sec.get("lyrics") or [])
            if (l.get("text") or "").strip()
        ]
        if sec_lyrs:
          stage2_lines.append(f"\n[{sec_name} {s_t:.0f}s-{e_t:.0f}s]")
          stage2_lines.extend(sec_lyrs)
        else:
          stage2_lines.append(
              f"\n[{sec_name} {s_t:.0f}s-{e_t:.0f}s] (Instrumental arrangement sustaining through {e_t:.0f}s)"
          )
    else:
      stage2_lines.append(
          f"\nInstrumental Structure: Intro (0s-{max(10, int(outro_start_anchor*0.15))}s), Main Theme, Dynamic Bridge, Climax, and Sustained Resolving Coda ({outro_start_anchor:.0f}s-{dur:.0f}s)."
      )
    self_healing_stage2_prompt = "\n".join(stage2_lines)

    # Stage 3: Ultra-clean direct vocal/duration recovery prompt (runs with fresh unseeded sampling if Stage 1 & 2 fail)
    stage3_lines = [
        f"Generate a complete {int(round(dur))}-second studio song at {bpm:.0f} BPM (duration_secs={int(round(dur))}).",
        f"Musical Style: {cls.sanitize_prompt_for_self_healing(global_cap)}",
        f"Vocal Performance: {'Instrumental only, no vocals.' if is_instrumental else cls.sanitize_prompt_for_self_healing(vocals_cap)}",
        f"Structure: Full-length arrangement sustaining through {dur:.0f}s with a resolving instrumental coda from {outro_start_anchor:.0f}s to {dur:.0f}s.",
    ]
    if not is_instrumental and top_level_lyrics:
      stage3_lines.append("\nLyrics:")
      for sec in sections:
        s_t = float(sec.get("start_sec") or 0.0)
        e_t = float(sec.get("end_sec") or dur)
        sec_name = sec.get("section_name", "Verse")
        sec_lyrs = [
            (l.get("text") or "").strip()
            for l in (sec.get("lyrics") or [])
            if (l.get("text") or "").strip()
        ]
        if sec_lyrs:
          stage3_lines.append(f"\n[{sec_name} {s_t:.0f}s-{e_t:.0f}s]")
          stage3_lines.extend(sec_lyrs)
        else:
          stage3_lines.append(f"\n[{sec_name} {s_t:.0f}s-{e_t:.0f}s]")
    self_healing_stage3_prompt = "\n".join(stage3_lines)

    return {
        "lyria3_transpiled_prompt": lyria3_prompt,
        "lyria3_timed_transcript": lyria3_transcript,
        "lyria3_song_plan_markdown": lyria3_markdown,
        "lyria3_music_gen_json": music_gen_spec,
        "vertex_engineered_prompt": "\n".join(vertex_lines),
        "self_healing_stage2_prompt": self_healing_stage2_prompt,
        "self_healing_stage3_prompt": self_healing_stage3_prompt,
    }

  @classmethod
  def generate_audio_with_lyria(
      cls,
      prompt_to_send: str,
      lyria_model: str = "lyria-3-pro-preview",
      seed: int | None = None,
      negative_prompt: str = "",
      output_basename: str = "track",
      requested_duration_sec: float = 150.0,
      fallback_prompts: list[dict] | None = None,
      is_instrumental: bool = False,
  ) -> dict:
    """Executes Lyria 3 music generation with a 3-Stage Self-Healing Retry Engine.

    - Never downgrades to `lyria-002` (30s instrumental baseline).
    - Uses the canonical Vertex AI `location="global"` and `response_modalities=["AUDIO", "TEXT"]`
      required by `lyria-3-pro-preview` / `lyria-3-clip-preview` (avoiding invalid `["AUDIO"]`-only
      or legacy interactions calls that trigger 400 INVALID_ARGUMENT).
    - Verifies generated audio duration via `ffprobe` before accepting: if a full-song
      request (`clamped_duration_sec >= 60.0`) returns an anomalous short clip (`< 45.0s`)
      or empty audio (e.g., transient filter/rewriter issue), automatically advances to Stage 2
      (Compact Sanitized SongPlan) and Stage 3 (Direct Unseeded Vocal/Duration Recovery).
    """
    cfg = CONFIG_STORE.get()
    # Normalize any legacy model string to lyria-3-pro-preview
    eff_model = (
        "lyria-3-pro-preview"
        if not lyria_model or "002" in lyria_model.lower()
        else lyria_model
    )
    is_clip = "clip" in eff_model.lower()
    clamped_duration_sec = cls.clamp_duration_for_model(
        requested_duration_sec, eff_model
    )
    min_valid_sec = (
        15.0
        if is_clip
        else float(cfg.get("lyria_min_fullsong_valid_sec", 45.0))
    )
    self_healing_enabled = bool(cfg.get("lyria_self_healing_enabled", True))
    max_stages = (
        max(1, min(3, int(cfg.get("lyria_self_healing_max_stages", 3))))
        if self_healing_enabled
        else 1
    )

    stage_specs = [
        {
            "stage": 1,
            "stage_name": "stage1_canonical_multi_section",
            "prompt": prompt_to_send,
            "seed_offset": 0,
            "use_seed": True,
        }
    ]
    if fallback_prompts:
      default_stage_names = {
          2: "stage2_compact_sanitized_plan",
          3: "stage3_direct_vocal_recovery",
      }
      for fp in fallback_prompts:
        if len(stage_specs) >= max_stages:
          break
        next_stage_num = len(stage_specs) + 1
        if isinstance(fp, str):
          fp_prompt = fp.strip()
          fp_name = default_stage_names.get(
              next_stage_num, f"stage{next_stage_num}_self_healing"
          )
          fp_seed_offset = (next_stage_num - 1) * 101
          fp_use_seed = next_stage_num < 3
        elif isinstance(fp, dict):
          fp_prompt = (fp.get("prompt") or "").strip()
          fp_name = fp.get("stage_name") or default_stage_names.get(
              next_stage_num, f"stage{next_stage_num}_self_healing"
          )
          fp_seed_offset = int(
              fp.get("seed_offset", (next_stage_num - 1) * 101)
          )
          fp_use_seed = bool(fp.get("use_seed", next_stage_num < 3))
        else:
          continue
        if fp_prompt:
          stage_specs.append({
              "stage": next_stage_num,
              "stage_name": fp_name,
              "prompt": fp_prompt,
              "seed_offset": fp_seed_offset,
              "use_seed": fp_use_seed,
          })
    else:
      # Auto-build Stage 2 & Stage 3 sanitized recovery prompts if not pre-supplied
      if max_stages >= 2:
        stage_specs.append({
            "stage": 2,
            "stage_name": "stage2_compact_sanitized_plan",
            "prompt": cls.sanitize_prompt_for_self_healing(prompt_to_send),
            "seed_offset": 101,
            "use_seed": True,
        })
      if max_stages >= 3:
        stage_specs.append({
            "stage": 3,
            "stage_name": "stage3_direct_vocal_recovery",
            "prompt": (
                f"Generate a complete {clamped_duration_sec:.0f}-second studio song "
                f"(duration_secs={clamped_duration_sec:.0f}).\n"
                + cls.sanitize_prompt_for_self_healing(prompt_to_send[:3600])
            ),
            "seed_offset": 202,
            "use_seed": False,
        })

    t0 = time.time()
    log_runtime(
        "INFO",
        "LYRIA_SYNTH_START",
        f"Starting Lyria synthesis for '{output_basename}' (model={eff_model}, target_dur={clamped_duration_sec}s, min_valid_sec={min_valid_sec}s, max_stages={len(stage_specs)})",
        item_id=output_basename,
        extra={
            "output_basename": output_basename,
            "lyria_model": eff_model,
            "requested_duration_sec": requested_duration_sec,
            "clamped_duration_sec": clamped_duration_sec,
            "min_valid_sec": min_valid_sec,
            "max_stages": len(stage_specs),
            "is_instrumental": bool(is_instrumental),
            "seed": seed,
        },
    )
    tmp_audio_dir = Path(tempfile.gettempdir()) / "music_studio_audio_scratch"
    tmp_audio_dir.mkdir(parents=True, exist_ok=True)

    accepted_audio_bytes = None
    accepted_audio_mime = "audio/mpeg"
    accepted_route = ""
    accepted_stage_name = "stage1_canonical_multi_section"
    backup_short_candidate = None
    model_returned_texts = []
    self_healing_attempts = []
    errors = []

    for st_spec in stage_specs:
      st_num = st_spec["stage"]
      st_name = st_spec["stage_name"]
      st_prompt = st_spec["prompt"]
      st_seed = (
          (int(seed) + st_spec["seed_offset"])
          if (seed is not None and st_spec.get("use_seed", True))
          else None
      )
      cand_bytes = None
      cand_mime = "audio/mpeg"
      cand_route = ""
      st_errors = []
      st_t0 = time.time()

      log_runtime(
          "INFO",
          "LYRIA_STAGE_ATTEMPT",
          f"[{output_basename}] Attempting Stage {st_num}/{len(stage_specs)} ({st_name}, prompt_chars={len(st_prompt)}, seed={st_seed})",
          item_id=output_basename,
          extra={
              "stage": st_num,
              "stage_name": st_name,
              "prompt_chars": len(st_prompt),
              "prompt_preview": st_prompt[:280],
              "seed": st_seed,
          },
      )

      # Canonical Vertex AI GenAI SDK route (`location="global"`, `response_modalities=["AUDIO", "TEXT"]`)
      if HAS_GENAI_SDK:
        loc = "global"
        modalities = ["AUDIO", "TEXT"]
        try:
          client = get_genai_client(location_override=loc)
          for retry_idx in range(2):
            try:
              gc_kwargs = {"response_modalities": modalities}
              if st_seed is not None:
                gc_kwargs["seed"] = int(st_seed)
              resp = client.models.generate_content(
                  model=eff_model,
                  contents=st_prompt,
                  config=types.GenerateContentConfig(**gc_kwargs),
              )
              stage_texts = []
              finish_reasons = []
              finish_messages = []
              for cand in getattr(resp, "candidates", []) or []:
                fr = getattr(cand, "finish_reason", None)
                if fr is not None:
                  finish_reasons.append(str(fr))
                fm = getattr(cand, "finish_message", None)
                if fm:
                  finish_messages.append(str(fm))
                content = getattr(cand, "content", None)
                for part in getattr(content, "parts", []) or []:
                  if getattr(part, "text", None):
                    model_returned_texts.append(part.text)
                    stage_texts.append(part.text.strip())
                  inline = getattr(part, "inline_data", None)
                  if inline and getattr(inline, "data", None):
                    m_type = getattr(inline, "mime_type", "") or "audio/mpeg"
                    if m_type.startswith("audio/") or len(inline.data) > 1000:
                      cand_bytes = inline.data
                      cand_mime = m_type
              if cand_bytes:
                cand_route = f"genai.generate_content({eff_model}, location={loc}, modalities={modalities}, stage={st_name})"
                break
              else:
                pf = getattr(resp, "prompt_feedback", None)
                pf_str = str(pf) if pf else None
                txt_preview = " | ".join(t[:160] for t in stage_texts if t)[:240]
                detail_suffix = f" (finish_reason={finish_reasons or ['NONE']}" + (f", msg={finish_messages}" if finish_messages else "") + (f", text='{txt_preview}'" if txt_preview else "") + ")"
                no_aud_msg = f"SDK({loc},{modalities}) returned response without inline audio bytes{detail_suffix}"
                st_errors.append(no_aud_msg)
                log_runtime(
                    "WARNING",
                    "LYRIA_ROUTE_NO_AUDIO",
                    f"[{output_basename}] Stage {st_num} {no_aud_msg}",
                    item_id=output_basename,
                    extra={
                        "stage": st_num,
                        "stage_name": st_name,
                        "location": loc,
                        "modalities": modalities,
                        "finish_reasons": finish_reasons,
                        "finish_messages": finish_messages,
                        "prompt_feedback": pf_str,
                        "returned_text_preview": txt_preview,
                    },
                )
                break
            except Exception as inner_e:
              err_str = str(inner_e)
              is_transient = any(
                  code in err_str
                  for code in ("429", "503", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE_EXCEEDED")
              )
              if is_transient and retry_idx == 0:
                log_runtime(
                    "WARNING",
                    "LYRIA_TRANSIENT_BACKOFF",
                    f"[{output_basename}] Stage {st_num} transient error ({err_str[:160]}); backing off {1.5 * st_num:.1f}s and retrying",
                    item_id=output_basename,
                    extra={"stage": st_num, "stage_name": st_name, "error": err_str[:300]},
                )
                time.sleep(1.5 * st_num)
                continue
              st_errors.append(f"SDK({loc},{modalities}): {err_str}")
              log_runtime(
                  "WARNING",
                  "LYRIA_ROUTE_ERROR",
                  f"[{output_basename}] Stage {st_num} SDK({loc},{modalities}) error: {err_str}",
                  item_id=output_basename,
                  extra={
                      "stage": st_num,
                      "stage_name": st_name,
                      "location": loc,
                      "modalities": modalities,
                      "error": err_str[:500],
                  },
              )
              break
        except Exception as outer_e:
          st_errors.append(f"Client({loc}): {outer_e}")
          log_runtime(
              "WARNING",
              "LYRIA_CLIENT_ERROR",
              f"[{output_basename}] Stage {st_num} Client({loc}) init error: {outer_e}",
              item_id=output_basename,
              extra={"stage": st_num, "error": str(outer_e)},
          )

      if not cand_bytes:
        errors.extend(st_errors)
        self_healing_attempts.append({
            "stage": st_num,
            "stage_name": st_name,
            "status": "no_audio_returned",
            "errors": st_errors[:3],
        })
        log_runtime(
            "WARNING",
            "LYRIA_STAGE_EMPTY",
            f"[{output_basename}] Stage {st_num} ({st_name}) returned no audio after {round(time.time() - st_t0, 2)}s; advancing to next self-healing stage",
            item_id=output_basename,
            extra={"stage": st_num, "stage_name": st_name, "errors": st_errors[:3]},
        )
        continue

      # Probe candidate duration before accepting to guard against unexpected ~30s clip degradation
      cand_ext = ".wav" if "wav" in cand_mime.lower() else ".mp3"
      probe_tmp = tmp_audio_dir / f"probe_{output_basename}_s{st_num}_{uuid.uuid4().hex[:5]}{cand_ext}"
      probe_tmp.write_bytes(cand_bytes)
      cand_probe = probe_audio_file(probe_tmp)
      cand_dur = float(cand_probe.get("duration_sec", 0.0))
      probe_tmp.unlink(missing_ok=True)

      if (not is_clip) and clamped_duration_sec >= 60.0 and 0.0 < cand_dur < min_valid_sec:
        # Detected anomalous short (~30s) output on a full-song request!
        msg = (
            f"Stage {st_num} ({st_name}) returned short clip ({cand_dur}s < {min_valid_sec}s minimum for {clamped_duration_sec}s target)"
        )
        log_runtime(
            "WARNING",
            "LYRIA_SELF_HEALING_RETRY",
            f"[{output_basename}] {msg}; triggering next self-healing stage",
            item_id=output_basename,
            extra={
                "stage": st_num,
                "stage_name": st_name,
                "measured_duration_sec": cand_dur,
                "min_valid_sec": min_valid_sec,
                "target_duration_sec": clamped_duration_sec,
                "route": cand_route,
            },
        )
        errors.append(msg)
        self_healing_attempts.append({
            "stage": st_num,
            "stage_name": st_name,
            "status": "short_duration_anomaly_retry",
            "measured_duration_sec": cand_dur,
            "route": cand_route,
        })
        if backup_short_candidate is None or cand_dur > backup_short_candidate["dur"]:
          backup_short_candidate = {
              "bytes": cand_bytes,
              "mime": cand_mime,
              "route": cand_route,
              "stage_name": st_name,
              "dur": cand_dur,
          }
        continue

      accepted_audio_bytes = cand_bytes
      accepted_audio_mime = cand_mime
      accepted_route = cand_route
      accepted_stage_name = st_name
      self_healing_attempts.append({
          "stage": st_num,
          "stage_name": st_name,
          "status": "accepted",
          "measured_duration_sec": cand_dur,
          "route": cand_route,
      })
      log_runtime(
          "INFO",
          "LYRIA_SYNTH_ACCEPTED",
          f"[{output_basename}] Stage {st_num} ({st_name}) accepted: measured_dur={cand_dur}s (target={clamped_duration_sec}s, bytes={len(cand_bytes)}, route={cand_route})",
          item_id=output_basename,
          extra={
              "stage": st_num,
              "stage_name": st_name,
              "measured_duration_sec": cand_dur,
              "target_duration_sec": clamped_duration_sec,
              "audio_bytes": len(cand_bytes),
              "route": cand_route,
              "stage_latency_sec": round(time.time() - st_t0, 2),
          },
      )
      break

    if not accepted_audio_bytes and backup_short_candidate is not None:
      accepted_audio_bytes = backup_short_candidate["bytes"]
      accepted_audio_mime = backup_short_candidate["mime"]
      accepted_route = backup_short_candidate["route"] + " [fallback_after_retries]"
      accepted_stage_name = backup_short_candidate["stage_name"]
      log_runtime(
          "WARNING",
          "LYRIA_SYNTH_BACKUP_SHORT",
          f"[{output_basename}] All stages returned short clips; using best backup candidate ({backup_short_candidate['dur']}s from {accepted_stage_name})",
          item_id=output_basename,
          extra={"measured_duration_sec": backup_short_candidate["dur"], "stage_name": accepted_stage_name},
      )

    if not accepted_audio_bytes:
      err_summary = f"Lyria 3 audio generation failed across {len(stage_specs)} self-healing stages: {' | '.join(errors)}"
      log_runtime(
          "ERROR",
          "LYRIA_SYNTH_FAILED",
          f"[{output_basename}] {err_summary}",
          item_id=output_basename,
          extra={"errors": errors, "self_healing_attempts": self_healing_attempts},
      )
      raise RuntimeError(err_summary)

    ext = ".wav" if "wav" in accepted_audio_mime.lower() else ".mp3"
    # Perform intermediate audio probing & post-processing in `/tmp` RAM tmpfs
    # to avoid creating/deleting/mutating temporary files on GCS FUSE mounts.
    raw_file = tmp_audio_dir / f"{output_basename}_{uuid.uuid4().hex[:6]}_raw{ext}"
    tmp_final = tmp_audio_dir / f"{output_basename}_{uuid.uuid4().hex[:6]}.mp3"
    raw_file.write_bytes(accepted_audio_bytes)

    post_info = apply_universal_audio_postprocess(raw_file, tmp_final, cfg)
    raw_file.unlink(missing_ok=True)

    probe = probe_audio_file(tmp_final)
    actual_dur = float(probe.get("duration_sec", 0.0))

    final_file = GENERATED_DIR / f"{output_basename}.mp3"
    try:
      shutil.copyfile(str(tmp_final), str(final_file))
    except OSError:
      time.sleep(0.08)
      shutil.copyfile(str(tmp_final), str(final_file))

    gcs_res = GCS_MANAGER.upload_file(
        tmp_final,
        f"generations/{final_file.name}",
        content_type="audio/mpeg",
    )
    tmp_final.unlink(missing_ok=True)

    total_lat = round(time.time() - t0, 2)
    log_runtime(
        "INFO",
        "LYRIA_POSTPROCESS_AND_UPLOAD_DONE",
        f"[{output_basename}] Final track ready in {total_lat}s: duration={actual_dur}s (delta={round(actual_dur - clamped_duration_sec, 2)}s), tail_rms={post_info.get('tail_rms_db')}dB, fade={post_info.get('tail_fade_applied')}, gcs={gcs_res.get('gs_uri') or gcs_res.get('uploaded')}",
        item_id=output_basename,
        extra={
            "filename": final_file.name,
            "duration_sec": actual_dur,
            "clamped_target_duration_sec": clamped_duration_sec,
            "tail_rms_db": post_info.get("tail_rms_db"),
            "tail_fade_applied": post_info.get("tail_fade_applied", False),
            "loudnorm_applied": post_info.get("loudnorm_applied", False),
            "self_healing_triggered": len(self_healing_attempts) > 1,
            "self_healing_stage_used": accepted_stage_name,
            "generation_latency_sec": total_lat,
            "gcs_uploaded": gcs_res.get("uploaded", False),
            "gcs_uri": gcs_res.get("gs_uri", ""),
        },
    )

    return {
        "filename": final_file.name,
        "local_path": str(final_file),
        "audio_url": f"/api/audio/generated/{final_file.name}",
        "mime_type": "audio/mpeg",
        "requested_duration_sec": round(float(requested_duration_sec), 1),
        "clamped_target_duration_sec": round(float(clamped_duration_sec), 1),
        "duration_sec": actual_dur,
        "duration_delta_sec": round(actual_dur - clamped_duration_sec, 2),
        "tail_rms_db": post_info.get("tail_rms_db"),
        "tail_fade_applied": post_info.get("tail_fade_applied", False),
        "loudnorm_applied": post_info.get("loudnorm_applied", False),
        "size_bytes": probe.get("size_bytes", 0),
        "used_route": accepted_route,
        "self_healing_triggered": len(self_healing_attempts) > 1,
        "self_healing_stage_used": accepted_stage_name,
        "self_healing_attempts": self_healing_attempts,
        "generation_latency_sec": round(time.time() - t0, 2),
        "lyria_returned_trajectory_texts": model_returned_texts,
        "gcs": gcs_res,
    }


# ==============================================================================
# 7. Deep Music Analysis Engine (`gemini-4-argon`, `thinking_level="MEDIUM"`)
# ==============================================================================
def run_deep_music_analysis(
    audio_source: str,
    track_title_hint: str = "",
    reference_lyrics: str = "",
    reference_prompt: str = "",
    custom_system_prompt: str | None = None,
) -> dict:
  """Analyzes an audio track using DSP probing + `gemini-4-argon` (`thinking_level="MEDIUM"`)."""
  cfg = CONFIG_STORE.get()
  local_path, mime_type = resolve_audio_source_to_local(audio_source)
  probe = probe_audio_file(local_path)

  sys_prompt = (
      custom_system_prompt
      or cfg.get("prompts", {}).get("music_analysis_system_prompt")
      or DEFAULT_MUSIC_ANALYSIS_SYSTEM_PROMPT
  )

  context_lines = [
      "Perform a deep, comprehensive musicological, structural, vocal, lyrical, and mix/mastering analysis of this audio track.",
      f"Measured Audio Duration: {probe.get('duration_sec', 0.0)} seconds ({probe.get('sample_rate', 48000)} Hz, {probe.get('channels', 2)} ch).",
  ]
  if track_title_hint:
    context_lines.append(f"Track Title / ID Hint: {track_title_hint}")
  if reference_prompt:
    context_lines.append(f"Reference Prompt / Style Context: {reference_prompt}")
  if reference_lyrics:
    context_lines.append(f"Reference User Lyrics:\n{reference_lyrics}")

  t0 = time.time()
  client = get_genai_client()
  gemini_model = cfg.get("gemini_model", "gemini-4-argon")

  audio_part = types.Part.from_bytes(
      data=local_path.read_bytes(), mime_type=mime_type
  )
  gen_kwargs = {
      "system_instruction": sys_prompt,
      "temperature": 0.2,
      "response_mime_type": "application/json",
  }
  t_cfg = build_thinking_config(cfg.get("gemini_thinking_level", "MEDIUM"))
  if t_cfg is not None:
    gen_kwargs["thinking_config"] = t_cfg

  resp = client.models.generate_content(
      model=gemini_model,
      contents=[audio_part, "\n".join(context_lines)],
      config=types.GenerateContentConfig(**gen_kwargs),
  )
  analysis_data = extract_json_from_text(resp.text or "")
  usage_meta = extract_usage_from_response(
      resp,
      fallback_input_text=(sys_prompt or "") + "\n" + "\n".join(context_lines),
      fallback_output_text=resp.text or "",
      fallback_audio_sec=float(probe.get("duration_sec") or 0.0),
      include_thinking_est=True,
  )

  analysis_id = f"ana_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
  record = {
      "analysis_id": analysis_id,
      "created_at": datetime.now(timezone.utc).isoformat(),
      "model": gemini_model,
      "thinking_level": cfg.get("gemini_thinking_level", "MEDIUM"),
      "latency_sec": round(time.time() - t0, 2),
      "track_title_hint": track_title_hint,
      "audio_source": audio_source,
      "audio_probe": probe,
      "usage_metadata": usage_meta,
      "analysis": analysis_data,
  }

  out_file = ANALYSES_DIR / f"{analysis_id}.json"
  out_file.write_text(
      json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  record["gcs"] = GCS_MANAGER.upload_json(
      record, f"analyses/{analysis_id}.json"
  )
  return record


# ==============================================================================
# 8. Multi-Track Music Evaluation & Comparison Engine (`gemini-4-argon`)
# ==============================================================================
def run_multi_track_evaluation(
    tracks: list[dict],
    target_prompt: str = "",
    target_lyrics: str = "",
    target_genre: str = "",
    target_language: str = "",
    target_vocal_gender: str = "",
    is_instrumental: bool = False,
    blind_mode: bool = True,
    custom_system_prompt: str | None = None,
) -> dict:
  """Compares 2 to 6 music tracks simultaneously in a single `gemini-4-argon` (`thinking_level="MEDIUM"`) multimodal context."""
  if len(tracks) < 2:
    raise ValueError("Please provide at least 2 tracks to compare.")

  cfg = CONFIG_STORE.get()
  sys_prompt = (
      custom_system_prompt
      or cfg.get("prompts", {}).get("multi_track_judge_system_prompt")
      or DEFAULT_MULTI_TRACK_JUDGE_SYSTEM_PROMPT
  )

  contents = []
  resolved_tracks_meta = []

  spec_header = (
      "### TARGET GENERATION SPECIFICATION:\n"
      f"- Target Prompt / Style: {target_prompt or 'N/A'}\n"
      f"- Target Genre: {target_genre or 'N/A'}\n"
      f"- Target Sung Language: {'Instrumental (No Vocals)' if is_instrumental else (target_language or 'Auto')}\n"
      f"- Target Vocal Gender: {'Instrumental' if is_instrumental else (target_vocal_gender or 'Auto')}\n"
      f"- Is Instrumental Mode: {bool(is_instrumental)}\n"
      f"- Target User Lyrics:\n{target_lyrics or '(None / Model-generated or Instrumental)'}\n\n"
      "### CANDIDATE AUDIO TRACKS TO EVALUATE:\n"
  )
  contents.append(spec_header)

  def _resolve_one_track(idx_and_tr):
    idx, tr = idx_and_tr
    track_id = tr.get("track_id") or f"Track_{chr(64 + idx)}"
    real_label = tr.get("label") or track_id
    shown_label = f"Candidate {track_id}" if blind_mode else f"{track_id} ({real_label})"
    src = tr.get("audio_source") or tr.get("audio_url") or ""
    local_path, mime_type = resolve_audio_source_to_local(src)
    probe = probe_audio_file(local_path)
    return {
        "track_id": track_id,
        "real_label": real_label,
        "shown_label": shown_label,
        "audio_source": src,
        "duration_sec": probe.get("duration_sec", 0.0),
        "local_path": local_path,
        "mime_type": mime_type,
    }

  with ThreadPoolExecutor(max_workers=min(6, len(tracks))) as pool:
    resolved_items = list(pool.map(_resolve_one_track, enumerate(tracks, start=1)))

  for item in resolved_items:
    resolved_tracks_meta.append({
        "track_id": item["track_id"],
        "real_label": item["real_label"],
        "shown_label": item["shown_label"],
        "audio_source": item["audio_source"],
        "duration_sec": item["duration_sec"],
    })
    contents.append(
        f"\n--- [{item['shown_label']}] (track_id: \"{item['track_id']}\", measured duration: {item['duration_sec']}s) ---"
    )
    contents.append(
        types.Part.from_bytes(
            data=item["local_path"].read_bytes(), mime_type=item["mime_type"]
        )
    )

  contents.append(
      "\nNow evaluate all candidate tracks above against the Target Generation Specification "
      "and return the complete 6-Dimension Rubric JSON verdict."
  )

  t0 = time.time()
  client = get_genai_client()
  gemini_model = cfg.get("gemini_model", "gemini-4-argon")

  gen_kwargs = {
      "system_instruction": sys_prompt,
      "temperature": 0.2,
      "response_mime_type": "application/json",
  }
  t_cfg = build_thinking_config(cfg.get("gemini_thinking_level", "MEDIUM"))
  if t_cfg is not None:
    gen_kwargs["thinking_config"] = t_cfg

  resp = client.models.generate_content(
      model=gemini_model,
      contents=contents,
      config=types.GenerateContentConfig(**gen_kwargs),
  )
  verdict = extract_json_from_text(resp.text or "")
  eval_usage_meta = extract_usage_from_response(
      resp,
      fallback_input_text=(sys_prompt or "") + "\n" + spec_header,
      fallback_output_text=resp.text or "",
      fallback_audio_sec=sum(float(x.get("duration_sec") or 0.0) for x in resolved_tracks_meta),
      include_thinking_est=True,
  )

  # Re-attach real labels if blind_mode was enabled
  label_lookup = {m["track_id"]: m["real_label"] for m in resolved_tracks_meta}
  for te in verdict.get("track_evaluations", []) or []:
    tid = te.get("track_id", "")
    if tid in label_lookup:
      te["real_label"] = label_lookup[tid]

  eval_id = f"eval_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
  record = {
      "eval_id": eval_id,
      "created_at": datetime.now(timezone.utc).isoformat(),
      "judge_model": gemini_model,
      "thinking_level": cfg.get("gemini_thinking_level", "MEDIUM"),
      "blind_mode": blind_mode,
      "latency_sec": round(time.time() - t0, 2),
      "usage_metadata": eval_usage_meta,
      "target_spec": {
          "target_prompt": target_prompt,
          "target_lyrics": target_lyrics,
          "target_genre": target_genre,
          "target_language": target_language,
          "target_vocal_gender": target_vocal_gender,
          "is_instrumental": is_instrumental,
      },
      "tracks_meta": resolved_tracks_meta,
      "verdict": verdict,
  }

  out_file = EVALUATIONS_DIR / f"{eval_id}.json"
  out_file.write_text(
      json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  record["gcs"] = GCS_MANAGER.upload_json(
      record, f"evaluations/{eval_id}.json"
  )
  return record


# ==============================================================================
# 8B. Full-Chain Cost Calculation & Group Analytics Engine (`CostAnalyticsEngine`)
# ==============================================================================
class CostAnalyticsEngine:
  """Calculates end-to-end per-song and per-group (A / B / C / U) costs across:

  1. Stage 1 — Gemini 4 Arrangement & Prompt Enhancement (`gemini-4-argon` Compilation)
  2. Stage 2 — Lyria 3 Audio Synthesis (`lyria-3-pro-preview` / `lyria-3-clip-preview`)
  3. Stage 3 — Gemini 4 Deep Multimodal Music Analysis (`gemini-4-argon` Audio Analysis)
  4. Stage 4 — Multi-Track Evaluation Arena & GCS Storage Operations
  """

  @staticmethod
  def format_datetime_utc(iso_str: str) -> str:
    """Formats an ISO-8601 timestamp into `YYYY-MM-DD HH:MM:SS UTC`."""
    raw = (iso_str or "").strip()
    if not raw or raw == "-":
      return "-"
    try:
      dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
      return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
      return raw[:19].replace("T", " ") + " UTC"

  @staticmethod
  def get_pricing(cfg: dict | None = None) -> dict:
    if cfg is None:
      cfg = CONFIG_STORE.get()
    defaults = DEFAULT_PLATFORM_CONFIG.get("pricing", {})
    p = dict(defaults)
    if isinstance(cfg.get("pricing"), dict):
      p.update(cfg["pricing"])
    return p

  @staticmethod
  def _normalize_group(raw_group: str, item_id: str = "", mode: str = "") -> str:
    g = (raw_group or "").strip().upper()
    if g in ("A", "B", "C"):
      return g
    iid = (item_id or "").strip().upper()
    if iid.startswith(("A", "B", "C")) and len(iid) >= 2 and (iid[1].isdigit() or iid[1] == "_"):
      return iid[0]
    m = (mode or "").strip().lower()
    if "group_a" in m:
      return "A"
    if "group_b" in m:
      return "B"
    if "group_c" in m:
      return "C"
    return "U"

  @classmethod
  def _load_analyses_lookup(cls) -> dict[str, dict]:
    """Builds a lookup map from audio_source / item_id / filename to analysis record."""
    lookup: dict[str, dict] = {}
    for p in sorted(ANALYSES_DIR.glob("ana_*.json"), reverse=True):
      try:
        d = json.loads(p.read_text(encoding="utf-8"))
        src = str(d.get("audio_source") or "").strip()
        hint = str(d.get("track_title_hint") or "").strip()
        if src and src not in lookup:
          lookup[src] = d
          fname = Path(src.split("?")[0]).name
          if fname and fname not in lookup:
            lookup[fname] = d
        if hint and hint not in lookup:
          lookup[hint.upper()] = d
      except Exception:
        pass
    return lookup

  @classmethod
  def compute_single_generation_cost(
      cls,
      rec: dict,
      analyses_lookup: dict[str, dict] | None = None,
      cfg: dict | None = None,
  ) -> dict:
    """Computes the full-chain cost breakdown for a single generated song record (`gen_*.json`)."""
    pricing = cls.get_pricing(cfg)
    usd_to_cny = float(pricing.get("currency_usd_to_cny") or 7.25)
    in_rate = float(pricing.get("gemini_input_per_1m_usd") or 1.25)
    out_rate = float(pricing.get("gemini_output_per_1m_usd") or 10.00)
    think_rate = float(pricing.get("gemini_thinking_per_1m_usd") or out_rate)
    audio_tok_per_sec = float(pricing.get("gemini_audio_tokens_per_sec") or 32.0)

    item_id = str(rec.get("item_id") or "custom")
    mode = str(rec.get("mode") or "group_a_lyrics")
    group = cls._normalize_group(str(rec.get("group") or ""), item_id, mode)
    inp = rec.get("input") or {}
    song_plan = rec.get("song_plan") or {}
    use_compiler = bool(rec.get("use_gemini_compiler", bool(song_plan)))
    compile_only = bool(rec.get("compile_only", False))
    lyria_model = str(rec.get("lyria_model") or "lyria-3-pro-preview")
    is_clip = "clip" in lyria_model.lower()

    # 1. Stage 1: Gemini 4 Arrangement & Prompt Enhancement Cost
    if use_compiler and song_plan:
      comp_usage = song_plan.get("_compiler_usage")
      if isinstance(comp_usage, dict) and comp_usage.get("total_tokens"):
        c_in_tok = int(comp_usage.get("input_tokens") or 0)
        c_out_tok = int(comp_usage.get("output_tokens") or 0)
        c_think_tok = int(comp_usage.get("thinking_tokens") or 0)
        comp_source = str(comp_usage.get("source") or "vertex_usage_metadata")
      else:
        user_p = str(inp.get("user_prompt") or "")
        user_l = str(inp.get("user_lyrics") or "")
        c_in_tok = 1150 + int(round((len(user_p) + len(user_l)) / 3.5))
        plan_json_len = len(json.dumps(song_plan.get("sections") or [], ensure_ascii=False)) + 320
        c_out_tok = max(380, int(round(plan_json_len / 3.6)))
        c_think_tok = int(round(c_out_tok * 0.55))
        comp_source = "estimated_from_payload"
      compiler_cost_usd = (
          (c_in_tok / 1_000_000.0) * in_rate
          + (c_out_tok / 1_000_000.0) * out_rate
          + (c_think_tok / 1_000_000.0) * think_rate
      )
    else:
      c_in_tok = 0
      c_out_tok = 0
      c_think_tok = 0
      comp_source = "disabled"
      compiler_cost_usd = 0.0

    # 2. Stage 2: Lyria 3 Audio Synthesis Cost
    parallel_tracks = rec.get("parallel_tracks") or ([rec["audio"]] if rec.get("audio") else [])
    primary_audio = rec.get("audio") or (parallel_tracks[0] if parallel_tracks else {})
    target_dur = float(
        primary_audio.get("clamped_target_duration_sec")
        or inp.get("target_duration_sec")
        or song_plan.get("duration_secs")
        or (30.0 if is_clip else 145.0)
    )
    measured_dur = float(primary_audio.get("duration_sec") or (0.0 if compile_only else target_dur))

    if compile_only or not parallel_tracks:
      tracks_generated = 0
      total_attempts = 0
      billable_tracks = 0
      total_generated_audio_sec = 0.0
      lyria_cost_usd = 0.0
      self_healing_stage = "compile_only"
    else:
      tracks_generated = len(parallel_tracks)
      total_attempts = sum(
          max(1, len(t.get("self_healing_attempts") or [1])) for t in parallel_tracks
      )
      bill_failed = bool(pricing.get("bill_failed_self_healing_attempts", False))
      billable_tracks = total_attempts if bill_failed else tracks_generated
      total_generated_audio_sec = sum(
          float(t.get("duration_sec") or measured_dur or target_dur) for t in parallel_tracks
      )
      billing_mode = str(pricing.get("lyria_billing_mode") or "per_track").lower()
      if billing_mode == "per_second":
        sec_rate = float(
            pricing.get("lyria_clip_per_second_usd" if is_clip else "lyria_pro_per_second_usd")
            or (0.0013 if is_clip else 0.0006)
        )
        lyria_cost_usd = total_generated_audio_sec * sec_rate
      else:
        track_rate = float(
            pricing.get("lyria_clip_per_track_usd" if is_clip else "lyria_pro_per_track_usd")
            or (0.040 if is_clip else 0.080)
        )
        lyria_cost_usd = billable_tracks * track_rate
      self_healing_stage = str(
          primary_audio.get("self_healing_stage_used") or "stage1_canonical_multi_section"
      )

    # 3. Stage 3: Gemini 4 Multimodal Deep Audio Analysis Cost
    matched_ana = None
    if analyses_lookup:
      cand_keys = [
          str(primary_audio.get("audio_url") or ""),
          str(primary_audio.get("filename") or ""),
          str((primary_audio.get("gcs") or {}).get("gs_uri") or ""),
          item_id.upper(),
          str(rec.get("generation_id") or "").upper(),
      ]
      for ck in cand_keys:
        if ck and ck in analyses_lookup:
          matched_ana = analyses_lookup[ck]
          break

    eff_audio_sec_for_ana = measured_dur if measured_dur > 0 else target_dur
    if matched_ana:
      ana_um = matched_ana.get("usage_metadata") or {}
      a_in_tok = int(
          ana_um.get("input_tokens")
          or (int(round(eff_audio_sec_for_ana * audio_tok_per_sec)) + 680)
      )
      a_out_tok = int(ana_um.get("output_tokens") or 920)
      a_think_tok = int(ana_um.get("thinking_tokens") or 560)
      ana_status = "measured"
      ana_status_label = "已实测分析"
      ana_cost_usd = (
          (a_in_tok / 1_000_000.0) * in_rate
          + (a_out_tok / 1_000_000.0) * out_rate
          + (a_think_tok / 1_000_000.0) * think_rate
      )
      ana_incurred_cost_usd = ana_cost_usd
    else:
      a_in_tok = int(round(eff_audio_sec_for_ana * audio_tok_per_sec)) + 680
      a_out_tok = 920
      a_think_tok = 560
      ana_status = "projected_by_duration"
      ana_status_label = "按实测曲长标准折算"
      ana_cost_usd = (
          (a_in_tok / 1_000_000.0) * in_rate
          + (a_out_tok / 1_000_000.0) * out_rate
          + (a_think_tok / 1_000_000.0) * think_rate
      )
      ana_incurred_cost_usd = 0.0

    # 4. GCS Storage & Operations micro-cost
    gcs_cost_usd = tracks_generated * float(pricing.get("gcs_ops_per_track_usd") or 0.0002)

    actual_total_usd = compiler_cost_usd + lyria_cost_usd + ana_incurred_cost_usd + gcs_cost_usd
    full_chain_total_usd = compiler_cost_usd + lyria_cost_usd + ana_cost_usd + gcs_cost_usd
    cost_per_sec_usd = full_chain_total_usd / max(1.0, eff_audio_sec_for_ana)

    raw_created_at = rec.get("created_at") or ""
    created_at_fmt = cls.format_datetime_utc(raw_created_at) if raw_created_at else "-"

    return {
        "generation_id": rec.get("generation_id") or "",
        "item_id": item_id,
        "group": group,
        "created_at": raw_created_at,
        "created_at_formatted": created_at_fmt,
        "record_type": "compile_only" if compile_only else "generated",
        "genre": inp.get("genre") or "Pop",
        "language": "Instrumental" if inp.get("is_instrumental") else (inp.get("language") or "English"),
        "is_instrumental": bool(inp.get("is_instrumental", False)),
        "target_duration_sec": round(target_dur, 2),
        "measured_duration_sec": round(measured_dur, 2),
        "compiler_model": song_plan.get("_compiler_model") or (cfg or {}).get("gemini_model", "gemini-4-argon"),
        "use_gemini_compiler": use_compiler,
        "compiler_usage_source": comp_source,
        "compiler_input_tokens": c_in_tok,
        "compiler_output_tokens": c_out_tok,
        "compiler_thinking_tokens": c_think_tok,
        "compiler_total_tokens": c_in_tok + c_out_tok + c_think_tok,
        "compiler_cost_usd": round(compiler_cost_usd, 6),
        "compiler_cost_cny": round(compiler_cost_usd * usd_to_cny, 4),
        "lyria_model": lyria_model,
        "lyria_billing_mode": pricing.get("lyria_billing_mode", "per_track"),
        "tracks_generated": tracks_generated,
        "total_lyria_attempts": total_attempts,
        "billable_lyria_tracks": billable_tracks,
        "self_healing_stage": self_healing_stage,
        "lyria_cost_usd": round(lyria_cost_usd, 6),
        "lyria_cost_cny": round(lyria_cost_usd * usd_to_cny, 4),
        "analysis_status": ana_status,
        "analysis_status_label": ana_status_label,
        "analysis_input_tokens": a_in_tok,
        "analysis_output_tokens": a_out_tok,
        "analysis_thinking_tokens": a_think_tok,
        "analysis_total_tokens": a_in_tok + a_out_tok + a_think_tok,
        "analysis_cost_usd": round(ana_cost_usd, 6),
        "analysis_cost_cny": round(ana_cost_usd * usd_to_cny, 4),
        "analysis_incurred_cost_usd": round(ana_incurred_cost_usd, 6),
        "gcs_cost_usd": round(gcs_cost_usd, 6),
        "actual_total_cost_usd": round(actual_total_usd, 6),
        "actual_total_cost_cny": round(actual_total_usd * usd_to_cny, 4),
        "full_chain_cost_usd": round(full_chain_total_usd, 6),
        "full_chain_cost_cny": round(full_chain_total_usd * usd_to_cny, 4),
        "cost_per_audio_sec_usd": round(cost_per_sec_usd, 6),
        "cost_per_audio_sec_cny": round(cost_per_sec_usd * usd_to_cny, 5),
        "audio_url": primary_audio.get("audio_url") or "",
        "gcs_uri": (primary_audio.get("gcs") or {}).get("gs_uri") or "",
    }

  @classmethod
  def compute_dataset_item_projected_cost(
      cls,
      item: dict,
      latest_gen_cost: dict | None = None,
      analyses_lookup: dict[str, dict] | None = None,
      cfg: dict | None = None,
  ) -> dict:
    """Computes full-chain cost for a dataset item (using its latest generation if available, or projecting from its prompt/lyrics/duration)."""
    if latest_gen_cost and latest_gen_cost.get("record_type") == "generated":
      row = dict(latest_gen_cost)
      row["dataset_status"] = "generated"
      row["dataset_status_label"] = "已生成实测"
      return row

    if cfg is None:
      cfg = CONFIG_STORE.get()
    pricing = cls.get_pricing(cfg)
    usd_to_cny = float(pricing.get("currency_usd_to_cny") or 7.25)
    in_rate = float(pricing.get("gemini_input_per_1m_usd") or 1.25)
    out_rate = float(pricing.get("gemini_output_per_1m_usd") or 10.00)
    think_rate = float(pricing.get("gemini_thinking_per_1m_usd") or out_rate)
    audio_tok_per_sec = float(pricing.get("gemini_audio_tokens_per_sec") or 32.0)

    item_id = str(item.get("id") or item.get("seq") or "item")
    group = cls._normalize_group(str(item.get("group") or "A"), item_id, "")
    is_inst = bool(item.get("is_instrumental")) or group == "C"
    user_p = str(item.get("prompt") or "")
    user_l = str(item.get("user_lyrics") or "")
    genre = str(item.get("genre") or "Pop")
    raw_style = str(item.get("raw_style") or genre)
    lang = "Instrumental" if is_inst else str(item.get("language") or "English")
    mode = (
        "group_a_lyrics"
        if group == "A"
        else ("group_b_prompt" if group == "B" else "group_c_instrumental")
    )
    est_dur = float(
        item.get("target_duration_sec")
        or LyriaBestPracticeEngine.estimate_optimal_duration_sec(
            mode=mode,
            user_prompt=user_p,
            user_lyrics=user_l,
            genre=genre,
            raw_style=raw_style,
            is_instrumental=is_inst,
            requested_duration_param=item.get("requested_duration_param", "auto"),
        )
    )
    lyria_model = str(cfg.get("lyria_default_model") or "lyria-3-pro-preview")
    is_clip = "clip" in lyria_model.lower()
    clamped_dur = LyriaBestPracticeEngine.clamp_duration_for_model(est_dur, lyria_model)

    # Stage 1: Gemini Compiler projection
    c_in_tok = 1150 + int(round((len(user_p) + len(user_l)) / 3.5))
    c_out_tok = 780 if is_inst else (960 if group == "A" else 1120)
    c_think_tok = int(round(c_out_tok * 0.55))
    compiler_cost_usd = (
        (c_in_tok / 1_000_000.0) * in_rate
        + (c_out_tok / 1_000_000.0) * out_rate
        + (c_think_tok / 1_000_000.0) * think_rate
    )

    # Stage 2: Lyria 3 synthesis projection
    billing_mode = str(pricing.get("lyria_billing_mode") or "per_track").lower()
    if billing_mode == "per_second":
      sec_rate = float(
          pricing.get("lyria_clip_per_second_usd" if is_clip else "lyria_pro_per_second_usd")
          or (0.0013 if is_clip else 0.0006)
      )
      lyria_cost_usd = clamped_dur * sec_rate
    else:
      track_rate = float(
          pricing.get("lyria_clip_per_track_usd" if is_clip else "lyria_pro_per_track_usd")
          or (0.040 if is_clip else 0.080)
      )
      lyria_cost_usd = track_rate

    # Stage 3: Gemini 4 Analysis projection
    a_in_tok = int(round(clamped_dur * audio_tok_per_sec)) + 680
    a_out_tok = 920
    a_think_tok = 560
    ana_cost_usd = (
        (a_in_tok / 1_000_000.0) * in_rate
        + (a_out_tok / 1_000_000.0) * out_rate
        + (a_think_tok / 1_000_000.0) * think_rate
    )
    gcs_cost_usd = float(pricing.get("gcs_ops_per_track_usd") or 0.0002)
    full_chain_total_usd = compiler_cost_usd + lyria_cost_usd + ana_cost_usd + gcs_cost_usd
    cost_per_sec_usd = full_chain_total_usd / max(1.0, clamped_dur)

    return {
        "generation_id": "(待生成预估)",
        "item_id": item_id,
        "group": group,
        "created_at": "-",
        "created_at_formatted": "(待生成预估)",
        "record_type": "projected",
        "dataset_status": "projected",
        "dataset_status_label": "题单预算预估",
        "genre": genre,
        "language": lang,
        "is_instrumental": is_inst,
        "target_duration_sec": round(clamped_dur, 2),
        "measured_duration_sec": round(clamped_dur, 2),
        "compiler_model": cfg.get("gemini_model", "gemini-4-argon"),
        "use_gemini_compiler": True,
        "compiler_usage_source": "projected",
        "compiler_input_tokens": c_in_tok,
        "compiler_output_tokens": c_out_tok,
        "compiler_thinking_tokens": c_think_tok,
        "compiler_total_tokens": c_in_tok + c_out_tok + c_think_tok,
        "compiler_cost_usd": round(compiler_cost_usd, 6),
        "compiler_cost_cny": round(compiler_cost_usd * usd_to_cny, 4),
        "lyria_model": lyria_model,
        "lyria_billing_mode": billing_mode,
        "tracks_generated": 1,
        "total_lyria_attempts": 1,
        "billable_lyria_tracks": 1,
        "self_healing_stage": "stage1_canonical_multi_section",
        "lyria_cost_usd": round(lyria_cost_usd, 6),
        "lyria_cost_cny": round(lyria_cost_usd * usd_to_cny, 4),
        "analysis_status": "projected_by_duration",
        "analysis_status_label": "按目标曲长预估",
        "analysis_input_tokens": a_in_tok,
        "analysis_output_tokens": a_out_tok,
        "analysis_thinking_tokens": a_think_tok,
        "analysis_total_tokens": a_in_tok + a_out_tok + a_think_tok,
        "analysis_cost_usd": round(ana_cost_usd, 6),
        "analysis_cost_cny": round(ana_cost_usd * usd_to_cny, 4),
        "analysis_incurred_cost_usd": 0.0,
        "gcs_cost_usd": round(gcs_cost_usd, 6),
        "actual_total_cost_usd": 0.0,
        "actual_total_cost_cny": 0.0,
        "full_chain_cost_usd": round(full_chain_total_usd, 6),
        "full_chain_cost_cny": round(full_chain_total_usd * usd_to_cny, 4),
        "cost_per_audio_sec_usd": round(cost_per_sec_usd, 6),
        "cost_per_audio_sec_cny": round(cost_per_sec_usd * usd_to_cny, 5),
        "audio_url": item.get("result_audio_url") or "",
        "gcs_uri": item.get("gcs_reference_uri") or "",
    }

  @classmethod
  def _summarize_rows_by_group(cls, rows: list[dict], usd_to_cny: float) -> dict:
    groups_meta = {
        "ALL": {"group": "ALL", "label": "全部组别 (All Groups)"},
        "A": {"group": "A", "label": "A组 · 歌词定制成曲 (Group A)"},
        "B": {"group": "B", "label": "B组 · 灵感写词谱曲 (Group B)"},
        "C": {"group": "C", "label": "C组 · 纯音乐与配乐 (Group C)"},
        "U": {"group": "U", "label": "U组 · 自定义与扩展 (Group U)"},
    }
    summary = {}
    for gk, gmeta in groups_meta.items():
      sub = rows if gk == "ALL" else [r for r in rows if r.get("group") == gk]
      cnt = len(sub)
      gen_cnt = sum(1 for r in sub if r.get("record_type") == "generated")
      total_dur = sum(float(r.get("measured_duration_sec") or 0.0) for r in sub)
      comp_usd = sum(float(r.get("compiler_cost_usd") or 0.0) for r in sub)
      lyria_usd = sum(float(r.get("lyria_cost_usd") or 0.0) for r in sub)
      ana_usd = sum(float(r.get("analysis_cost_usd") or 0.0) for r in sub)
      gcs_usd = sum(float(r.get("gcs_cost_usd") or 0.0) for r in sub)
      actual_usd = sum(float(r.get("actual_total_cost_usd") or 0.0) for r in sub)
      full_usd = sum(float(r.get("full_chain_cost_usd") or 0.0) for r in sub)
      comp_tok = sum(int(r.get("compiler_total_tokens") or 0) for r in sub)
      ana_tok = sum(int(r.get("analysis_total_tokens") or 0) for r in sub)

      summary[gk] = {
          "group": gk,
          "label": gmeta["label"],
          "song_count": cnt,
          "generated_count": gen_cnt,
          "total_duration_sec": round(total_dur, 1),
          "avg_duration_sec": round(total_dur / cnt, 1) if cnt else 0.0,
          "compiler_total_tokens": comp_tok,
          "analysis_total_tokens": ana_tok,
          "compiler_cost_usd": round(comp_usd, 5),
          "compiler_cost_cny": round(comp_usd * usd_to_cny, 4),
          "lyria_cost_usd": round(lyria_usd, 5),
          "lyria_cost_cny": round(lyria_usd * usd_to_cny, 4),
          "analysis_cost_usd": round(ana_usd, 5),
          "analysis_cost_cny": round(ana_usd * usd_to_cny, 4),
          "gcs_cost_usd": round(gcs_usd, 5),
          "actual_total_cost_usd": round(actual_usd, 5),
          "actual_total_cost_cny": round(actual_usd * usd_to_cny, 3),
          "full_chain_total_cost_usd": round(full_usd, 5),
          "full_chain_total_cost_cny": round(full_usd * usd_to_cny, 3),
          "avg_song_full_chain_usd": round(full_usd / cnt, 5) if cnt else 0.0,
          "avg_song_full_chain_cny": round((full_usd / cnt) * usd_to_cny, 4) if cnt else 0.0,
          "avg_cost_per_sec_usd": round(full_usd / max(1.0, total_dur), 6) if cnt else 0.0,
      }
    return summary

  @classmethod
  def build_cost_dashboard_report(cls) -> dict:
    """Builds the complete cost analytics report across both generated tracks and full A/B/C dataset items."""
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat()
    now_fmt = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    cfg = CONFIG_STORE.get()
    pricing = cls.get_pricing(cfg)
    usd_to_cny = float(pricing.get("currency_usd_to_cny") or 7.25)
    analyses_lookup = cls._load_analyses_lookup()

    generated_rows: list[dict] = []
    latest_by_item: dict[str, dict] = {}
    for p in sorted(
        GENERATED_DIR.glob("gen_*.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    ):
      try:
        rec = json.loads(p.read_text(encoding="utf-8"))
        if rec.get("compile_only"):
          continue
        c_row = cls.compute_single_generation_cost(rec, analyses_lookup=analyses_lookup, cfg=cfg)
        c_row["dataset_status"] = "generated"
        c_row["dataset_status_label"] = "已生成实测"
        generated_rows.append(c_row)
        iid = str(c_row.get("item_id") or "").upper()
        if iid and iid not in latest_by_item:
          latest_by_item[iid] = c_row
          if re.match(r"^[ABC]_[ABC]\d{2,3}$", iid):
            latest_by_item[iid.split("_", 1)[1]] = c_row
      except Exception:
        pass

    if (
        not BUILTIN_DATASET.get("items")
        and GCS_MANAGER.bucket_name
        and GCS_MANAGER.bucket_name not in ("your-gcs-bucket-name", "")
    ):
      try:
        GCS_MANAGER.discover_assets(
            scan_entire_bucket_if_empty=True,
            auto_register=True,
            auto_load_dataset=True,
        )
      except Exception:
        pass

    dataset_items = list(BUILTIN_DATASET.get("items") or [])
    existing_ds_ids = {str(it.get("id") or "").upper() for it in dataset_items if it.get("id")}
    for pl_path in sorted(UPLOADS_LISTS_DIR.glob("pl_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
      try:
        pl_obj = json.loads(pl_path.read_text(encoding="utf-8"))
        for it in pl_obj.get("items") or []:
          iid = str(it.get("id") or "").upper()
          if iid and iid not in existing_ds_ids:
            dataset_items.append(it)
            existing_ds_ids.add(iid)
      except Exception:
        pass

    all_dataset_rows: list[dict] = []
    seen_item_ids = set()
    for it in dataset_items:
      iid = str(it.get("id") or "").upper()
      short_seq = str(it.get("seq") or "").upper()
      matched_gen = latest_by_item.get(iid) or latest_by_item.get(short_seq)
      d_row = cls.compute_dataset_item_projected_cost(
          it, latest_gen_cost=matched_gen, analyses_lookup=analyses_lookup, cfg=cfg
      )
      all_dataset_rows.append(d_row)
      if matched_gen:
        seen_item_ids.add(str(matched_gen.get("generation_id") or ""))

    # Also include any generated tracks not matched by dataset_items (across A/B/C/U)
    for g_row in generated_rows:
      if g_row.get("generation_id") not in seen_item_ids:
        all_dataset_rows.append(g_row)

    gen_summary_by_group = cls._summarize_rows_by_group(generated_rows, usd_to_cny)
    ds_summary_by_group = cls._summarize_rows_by_group(all_dataset_rows, usd_to_cny)

    return {
        "generated_at": now_iso,
        "report_datetime_utc": now_fmt,
        "pricing": pricing,
        "generated_tracks_count": len(generated_rows),
        "dataset_total_count": len(all_dataset_rows),
        "generated_summary_by_group": gen_summary_by_group,
        "dataset_summary_by_group": ds_summary_by_group,
        "generated_rows": generated_rows,
        "all_dataset_rows": all_dataset_rows,
        "archived_reports": cls.list_archived_cost_reports(limit=25),
    }

  @classmethod
  def export_cost_csv_string(
      cls, group: str = "ALL", scope: str = "all_dataset", report: dict | None = None
  ) -> str:
    """Exports per-song full-chain cost breakdown to a UTF-8 with BOM CSV string, including explicit UTC date-time columns."""
    if report is None:
      report = cls.build_cost_dashboard_report()
    report_dt_str = report.get("report_datetime_utc") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rows = (
        report["generated_rows"]
        if (scope or "").lower() == "generated"
        else report["all_dataset_rows"]
    )
    grp_filter = (group or "ALL").strip().upper()
    if grp_filter in ("A", "B", "C", "U"):
      rows = [r for r in rows if r.get("group") == grp_filter]

    buf = io.StringIO()
    buf.write("\ufeff")
    writer = csv.writer(buf)
    writer.writerow([
        "序号 (Row)",
        "报表核算日期时间_UTC (Report_DateTime_UTC)",
        "组别 (Group)",
        "曲目ID (Item_ID)",
        "状态 (Status)",
        "生成记录ID (Generation_ID)",
        "单曲生成日期时间_UTC (Song_Created_DateTime_UTC)",
        "流派 (Genre)",
        "语种 (Language)",
        "是否纯音乐 (Is_Instrumental)",
        "目标时长_秒 (Target_Duration_Sec)",
        "实测/核算时长_秒 (Measured_Duration_Sec)",
        "阶段1_Gemini编曲模型 (Compiler_Model)",
        "阶段1_编曲输入Tokens (Compiler_Input_Tokens)",
        "阶段1_编曲输出Tokens (Compiler_Output_Tokens)",
        "阶段1_编曲思考Tokens (Compiler_Thinking_Tokens)",
        "阶段1_Gemini4编曲成本_USD (Compiler_Cost_USD)",
        "阶段2_Lyria生成模型 (Lyria_Model)",
        "阶段2_计费模式 (Lyria_Billing_Mode)",
        "阶段2_生成候选曲数 (Tracks_Generated)",
        "阶段2_自愈阶段 (Self_Healing_Stage)",
        "阶段2_Lyria3生成成本_USD (Lyria_Cost_USD)",
        "阶段3_Gemini4分析状态 (Analysis_Status)",
        "阶段3_分析总Tokens (Analysis_Total_Tokens)",
        "阶段3_Gemini4深度分析成本_USD (Analysis_Cost_USD)",
        "GCS归档微成本_USD (GCS_Cost_USD)",
        "已发生实测成本_USD (Actual_Incurred_Cost_USD)",
        "单曲全链路总成本_USD (Full_Chain_Cost_USD)",
        "单曲全链路总成本_CNY (Full_Chain_Cost_CNY)",
        "每秒音频全链路成本_USD (Cost_Per_Audio_Sec_USD)",
        "云端音频路径 (GCS_URI)",
    ])

    for idx, r in enumerate(rows, start=1):
      song_dt = r.get("created_at_formatted") or cls.format_datetime_utc(r.get("created_at", ""))
      writer.writerow([
          idx,
          report_dt_str,
          r.get("group", ""),
          r.get("item_id", ""),
          r.get("dataset_status_label", "已生成实测"),
          r.get("generation_id", ""),
          song_dt,
          r.get("genre", ""),
          r.get("language", ""),
          1 if r.get("is_instrumental") else 0,
          r.get("target_duration_sec", 0.0),
          r.get("measured_duration_sec", 0.0),
          r.get("compiler_model", ""),
          r.get("compiler_input_tokens", 0),
          r.get("compiler_output_tokens", 0),
          r.get("compiler_thinking_tokens", 0),
          f"{float(r.get('compiler_cost_usd', 0.0)):.6f}",
          r.get("lyria_model", ""),
          r.get("lyria_billing_mode", ""),
          r.get("tracks_generated", 1),
          r.get("self_healing_stage", ""),
          f"{float(r.get('lyria_cost_usd', 0.0)):.6f}",
          r.get("analysis_status_label", ""),
          r.get("analysis_total_tokens", 0),
          f"{float(r.get('analysis_cost_usd', 0.0)):.6f}",
          f"{float(r.get('gcs_cost_usd', 0.0)):.6f}",
          f"{float(r.get('actual_total_cost_usd', 0.0)):.6f}",
          f"{float(r.get('full_chain_cost_usd', 0.0)):.6f}",
          f"{float(r.get('full_chain_cost_cny', 0.0)):.4f}",
          f"{float(r.get('cost_per_audio_sec_usd', 0.0)):.6f}",
          r.get("gcs_uri", ""),
      ])

    return buf.getvalue()

  @classmethod
  def save_and_archive_cost_snapshot(
      cls,
      group: str = "ALL",
      scope: str = "all_dataset",
      trigger: str = "manual_export",
  ) -> dict:
    """Saves a timestamped CSV and JSON cost report to local `workspace/cost_reports/` AND archives both to GCS (`gs://<bucket>/<prefix>/cost_reports/`)."""
    now_dt = datetime.now(timezone.utc)
    ts_slug = now_dt.strftime("%Y%m%d_%H%M%S")
    ts_display = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    grp_clean = (group or "ALL").strip().upper()
    if grp_clean not in ("ALL", "A", "B", "C", "U"):
      grp_clean = "ALL"
    is_gen_scope = (scope or "").lower() == "generated"
    scope_clean = "generated" if is_gen_scope else "full_dataset"

    report = cls.build_cost_dashboard_report()
    csv_text = cls.export_cost_csv_string(group=grp_clean, scope=scope, report=report)

    csv_fname = f"musewave_cost_report_{scope_clean}_group_{grp_clean}_{ts_slug}.csv"
    json_fname = f"musewave_cost_ledger_{scope_clean}_group_{grp_clean}_{ts_slug}.json"
    meta_fname = f"snapshot_meta_{ts_slug}_{grp_clean}_{scope_clean}.json"

    local_csv_path = COST_REPORTS_DIR / csv_fname
    local_json_path = COST_REPORTS_DIR / json_fname
    latest_csv_path = COST_REPORTS_DIR / f"latest_cost_report_{scope_clean}_group_{grp_clean}.csv"

    local_csv_path.write_text(csv_text, encoding="utf-8")
    try:
      latest_csv_path.write_text(csv_text, encoding="utf-8")
    except Exception:
      pass

    summary_map = (
        report["generated_summary_by_group"]
        if is_gen_scope
        else report["dataset_summary_by_group"]
    )
    grp_summary = summary_map.get(grp_clean) or summary_map.get("ALL") or {}

    ledger_payload = {
        "snapshot_id": f"cost_{ts_slug}_{grp_clean}_{scope_clean}",
        "created_at": now_dt.isoformat(),
        "created_at_formatted": ts_display,
        "trigger": trigger,
        "group": grp_clean,
        "scope": scope,
        "pricing": report.get("pricing", {}),
        "group_summary": grp_summary,
        "all_groups_summary": summary_map,
        "rows_count": grp_summary.get("song_count", 0),
    }
    local_json_path.write_text(
        json.dumps(ledger_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    gcs_csv_res = GCS_MANAGER.upload_file(
        local_csv_path,
        f"cost_reports/{csv_fname}",
        content_type="text/csv; charset=utf-8",
    )
    gcs_json_res = GCS_MANAGER.upload_file(
        local_json_path,
        f"cost_reports/{json_fname}",
        content_type="application/json; charset=utf-8",
    )

    meta_entry = {
        "snapshot_id": ledger_payload["snapshot_id"],
        "created_at": now_dt.isoformat(),
        "created_at_formatted": ts_display,
        "trigger": trigger,
        "group": grp_clean,
        "scope": scope,
        "scope_label": "已生成实测曲目" if is_gen_scope else "A/B/C 全题单全景",
        "song_count": grp_summary.get("song_count", 0),
        "generated_count": grp_summary.get("generated_count", 0),
        "full_chain_total_cost_usd": grp_summary.get("full_chain_total_cost_usd", 0.0),
        "full_chain_total_cost_cny": grp_summary.get("full_chain_total_cost_cny", 0.0),
        "actual_total_cost_usd": grp_summary.get("actual_total_cost_usd", 0.0),
        "csv_filename": csv_fname,
        "json_filename": json_fname,
        "csv_download_url": f"/api/cost/archives/download/{csv_fname}",
        "json_download_url": f"/api/cost/archives/download/{json_fname}",
        "gcs_csv": gcs_csv_res,
        "gcs_json": gcs_json_res,
    }
    try:
      (COST_REPORTS_DIR / meta_fname).write_text(
          json.dumps(meta_entry, ensure_ascii=False, indent=2), encoding="utf-8"
      )
    except Exception:
      pass

    return meta_entry

  @classmethod
  def list_archived_cost_reports(cls, limit: int = 25) -> list[dict]:
    """Lists saved timestamped cost report snapshots from `workspace/cost_reports/` (newest first)."""
    items: list[dict] = []
    if not COST_REPORTS_DIR.exists():
      return items
    for p in sorted(
        COST_REPORTS_DIR.glob("snapshot_meta_*.json"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )[: max(1, min(100, limit))]:
      try:
        items.append(json.loads(p.read_text(encoding="utf-8")))
      except Exception:
        pass
    return items


# ==============================================================================
# 9. Single & Batch Generation Coordinator
# ==============================================================================
JOBS_REGISTRY: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOBS_PERSIST_LOCK = threading.Lock()
_JOBS_LAST_PERSIST_TS: dict[str, float] = {}
LOCAL_TMP_JOBS_DIR = Path(tempfile.gettempdir()) / "music_studio_jobs_cache"
LOCAL_TMP_JOBS_DIR.mkdir(parents=True, exist_ok=True)


def execute_single_generation_pipeline(req_data: dict) -> dict:
  """Runs the complete single-item generation pipeline:

  1. Optional Gemini (`thinking_level="MEDIUM"`) Lyria SongPlan & Multi-Caption compilation
  2. Lyria 3 audio synthesis (`lyria-3-pro-preview` / `lyria-3-clip-preview`) with 3-Stage Self-Healing Retry
  3. Post-synthesis duration verification & GCS archiving
  """
  cfg = CONFIG_STORE.get()
  item_id = req_data.get("item_id") or f"custom_{uuid.uuid4().hex[:6]}"
  mode = req_data.get("mode") or "group_a_lyrics"
  user_prompt = req_data.get("user_prompt") or ""
  user_lyrics = req_data.get("user_lyrics") or ""
  genre = req_data.get("genre") or "Pop"
  raw_style = req_data.get("raw_style") or genre
  language = req_data.get("language") or "English"
  vocal_gender = req_data.get("vocal_gender") or ""
  is_instrumental = bool(req_data.get("is_instrumental", False)) or mode == "group_c_instrumental"
  raw_dur_input = req_data.get("target_duration_sec")
  if (
      raw_dur_input is None
      or str(raw_dur_input).strip().lower() in ("", "auto", "0", "0.0")
  ):
    target_duration_sec = LyriaBestPracticeEngine.estimate_optimal_duration_sec(
        mode=mode,
        user_prompt=user_prompt,
        user_lyrics=user_lyrics,
        genre=genre,
        raw_style=raw_style,
        is_instrumental=is_instrumental,
        requested_duration_param="auto",
    )
  else:
    target_duration_sec = float(raw_dur_input)
  lyria_model = req_data.get("lyria_model") or cfg.get("lyria_default_model", "lyria-3-pro-preview")
  if "002" in lyria_model.lower():
    lyria_model = "lyria-3-pro-preview"
  clamped_dur = LyriaBestPracticeEngine.clamp_duration_for_model(
      target_duration_sec, lyria_model
  )
  use_compiler = req_data.get(
      "use_gemini_compiler", cfg.get("use_gemini_lyria_compiler", True)
  )
  seed = req_data.get("seed")
  compile_only = bool(req_data.get("compile_only", False))
  parallel_candidates = max(
      1,
      min(
          4,
          int(
              req_data.get("parallel_candidates")
              or cfg.get("single_parallel_candidates", 1)
          ),
      ),
  )
  ab_compare_raw = bool(req_data.get("ab_compare_raw", False))

  pipeline_t0 = time.time()
  log_runtime(
      "INFO",
      "GEN_PIPELINE_START",
      f"Starting generation pipeline for item='{item_id}' (mode={mode}, genre={genre}, lang={language}, dur={clamped_dur}s, model={lyria_model}, compiler={use_compiler}, candidates={parallel_candidates})",
      item_id=item_id,
      extra={
          "item_id": item_id,
          "mode": mode,
          "genre": genre,
          "language": language,
          "vocal_gender": vocal_gender,
          "is_instrumental": is_instrumental,
          "raw_duration_input": raw_dur_input,
          "target_duration_sec": target_duration_sec,
          "clamped_duration_sec": clamped_dur,
          "lyria_model": lyria_model,
          "use_gemini_compiler": use_compiler,
          "compile_only": compile_only,
          "parallel_candidates": parallel_candidates,
          "ab_compare_raw": ab_compare_raw,
      },
  )

  # Construct raw prompt baseline string (including explicit duration & ending guidance)
  raw_parts = [
      f"Target Duration: {clamped_dur:.0f} seconds (duration_secs={clamped_dur:.0f}, with resolving outro fade-out in the final 10 seconds).",
      f"Style & Genre: {genre} ({raw_style}). {user_prompt}",
  ]
  if is_instrumental:
    raw_parts.append("Instrumental only, no vocals.")
  else:
    if vocal_gender:
      raw_parts.append(f"Vocal Gender: {vocal_gender}.")
    if language:
      raw_parts.append(f"Sung Language: {language}.")
    if user_lyrics:
      raw_parts.append(f"Lyrics:\n{user_lyrics}")
  raw_prompt_str = "\n".join(raw_parts)

  # Build raw fallback prompts in case compiler is disabled or raw baseline is used
  clean_raw_style = LyriaBestPracticeEngine.sanitize_prompt_for_self_healing(
      f"{genre}, {raw_style}. {user_prompt}"
  )
  raw_fallback_stage2 = (
      f"Target Duration: {clamped_dur:.0f} seconds (duration_secs={clamped_dur:.0f}).\n"
      f"Style: {clean_raw_style}.\n"
      + (
          "Instrumental only, no vocals."
          if is_instrumental
          else f"Vocals: {vocal_gender or 'expressive lead vocal'} singing clearly in {language}.\nLyrics:\n{user_lyrics}"
      )
  )
  raw_fallback_stage3 = (
      f"Full-length {clamped_dur:.0f}-second {genre} track ({clean_raw_style}). "
      + (
          "Instrumental arrangement with Intro, Main Theme, Bridge, Climax, and Outro."
          if is_instrumental
          else f"Clear {vocal_gender or 'lead'} vocals in {language} from Verse 1 through Chorus and Outro.\n\n{user_lyrics}"
      )
  )

  song_plan = None
  fallback_prompts_for_lyria = [
      {
          "stage_name": "stage2_compact_sanitized_plan",
          "prompt": raw_fallback_stage2,
          "seed_offset": 101,
      },
      {
          "stage_name": "stage3_direct_vocal_recovery",
          "prompt": raw_fallback_stage3,
          "seed_offset": 202,
      },
  ]
  if use_compiler:
    song_plan = LyriaBestPracticeEngine.compile_song_plan_with_gemini(
        mode=mode,
        user_prompt=user_prompt,
        user_lyrics=user_lyrics,
        genre=genre,
        raw_style=raw_style,
        language=language,
        vocal_gender=vocal_gender,
        is_instrumental=is_instrumental,
        target_duration_sec=target_duration_sec,
        lyria_model=lyria_model,
        custom_system_prompt=req_data.get("custom_compiler_prompt"),
    )
    prompt_for_lyria = (
        req_data.get("override_engineered_prompt")
        or song_plan.get("vertex_engineered_prompt")
        or raw_prompt_str
    )
    s2 = song_plan.get("self_healing_stage2_prompt") or raw_fallback_stage2
    s3 = song_plan.get("self_healing_stage3_prompt") or raw_fallback_stage3
    fallback_prompts_for_lyria = [
        {
            "stage_name": "stage2_compact_sanitized_plan",
            "prompt": s2,
            "seed_offset": 101,
        },
        {
            "stage_name": "stage3_direct_vocal_recovery",
            "prompt": s3,
            "seed_offset": 202,
        },
    ]
  else:
    prompt_for_lyria = raw_prompt_str

  gen_id = f"gen_{item_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
  group_code = (
      req_data.get("group")
      or (item_id[0].upper() if item_id and item_id[0].upper() in ("A", "B", "C") else "U")
  )

  if compile_only:
    plan_record = {
        "generation_id": gen_id,
        "item_id": item_id,
        "group": group_code,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "compile_only": True,
        "mode": mode,
        "lyria_model": lyria_model,
        "input": {
            "user_prompt": user_prompt,
            "user_lyrics": user_lyrics,
            "genre": genre,
            "raw_style": raw_style,
            "language": language,
            "vocal_gender": vocal_gender,
            "is_instrumental": is_instrumental,
            "target_duration_sec": target_duration_sec,
            "reference_audio_url": req_data.get("reference_audio_url", ""),
        },
        "song_plan": song_plan,
        "prompt_for_lyria": prompt_for_lyria,
    }
    plan_record["cost_breakdown"] = CostAnalyticsEngine.compute_single_generation_cost(
        plan_record, cfg=cfg
    )
    meta_path = GENERATED_DIR / f"{gen_id}.json"
    meta_path.write_text(
        json.dumps(plan_record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    plan_record["metadata_gcs"] = GCS_MANAGER.upload_json(
        plan_record, f"generations/{gen_id}.json"
    )
    log_runtime(
        "INFO",
        "GEN_PIPELINE_COMPILE_ONLY_DONE",
        f"Compile-only SongPlan completed for '{item_id}' (gen_id={gen_id}) in {round(time.time() - pipeline_t0, 2)}s",
        item_id=item_id,
        extra={"generation_id": gen_id, "latency_sec": round(time.time() - pipeline_t0, 2)},
    )
    return plan_record

  # Build parallel synthesis specs
  synth_specs = []
  base_seed = int(seed) if seed is not None else None
  for idx in range(parallel_candidates):
    cand_seed = (base_seed + idx) if base_seed is not None else None
    suffix = f"_v{idx + 1}" if parallel_candidates > 1 or ab_compare_raw else ""
    synth_specs.append({
        "variant_id": f"Candidate_{idx + 1}",
        "variant_label": (
            f"Lyria ({lyria_model}) · Gemini增强 #{idx + 1}"
            if use_compiler
            else f"Lyria ({lyria_model}) · Raw #{idx + 1}"
        ),
        "prompt": prompt_for_lyria,
        "fallback_prompts": fallback_prompts_for_lyria,
        "seed": cand_seed,
        "basename": f"{gen_id}{suffix}",
    })

  if ab_compare_raw and use_compiler:
    synth_specs.append({
        "variant_id": "Raw_Baseline",
        "variant_label": f"Lyria ({lyria_model}) · 原始Prompt基线 (无Gemini编译)",
        "prompt": raw_prompt_str,
        "fallback_prompts": [
            {
                "stage_name": "stage2_compact_sanitized_plan",
                "prompt": raw_fallback_stage2,
                "seed_offset": 101,
            },
            {
                "stage_name": "stage3_direct_vocal_recovery",
                "prompt": raw_fallback_stage3,
                "seed_offset": 202,
            },
        ],
        "seed": base_seed,
        "basename": f"{gen_id}_raw_baseline",
    })

  def _synth_one(spec):
    aud = LyriaBestPracticeEngine.generate_audio_with_lyria(
        prompt_to_send=spec["prompt"],
        lyria_model=lyria_model,
        seed=spec["seed"],
        output_basename=spec["basename"],
        requested_duration_sec=target_duration_sec,
        fallback_prompts=spec.get("fallback_prompts"),
        is_instrumental=is_instrumental,
    )
    aud["variant_id"] = spec["variant_id"]
    aud["variant_label"] = spec["variant_label"]
    return aud

  if len(synth_specs) == 1:
    parallel_tracks = [_synth_one(synth_specs[0])]
  else:
    with ThreadPoolExecutor(max_workers=len(synth_specs)) as pool:
      futures = [
          pool.submit(contextvars.copy_context().run, _synth_one, sp)
          for sp in synth_specs
      ]
      parallel_tracks = [f.result() for f in futures]

  audio_res = parallel_tracks[0]

  record = {
      "generation_id": gen_id,
      "item_id": item_id,
      "group": group_code,
      "created_at": datetime.now(timezone.utc).isoformat(),
      "compile_only": False,
      "mode": mode,
      "lyria_model": lyria_model,
      "use_gemini_compiler": use_compiler,
      "parallel_candidates_count": len(parallel_tracks),
      "input": {
          "user_prompt": user_prompt,
          "user_lyrics": user_lyrics,
          "genre": genre,
          "raw_style": raw_style,
          "language": language,
          "vocal_gender": vocal_gender,
          "is_instrumental": is_instrumental,
          "target_duration_sec": target_duration_sec,
          "reference_audio_url": req_data.get("reference_audio_url", ""),
      },
      "song_plan": song_plan,
      "prompt_for_lyria": prompt_for_lyria,
      "audio": audio_res,
      "parallel_tracks": parallel_tracks,
  }
  record["cost_breakdown"] = CostAnalyticsEngine.compute_single_generation_cost(
      record, cfg=cfg
  )

  meta_path = GENERATED_DIR / f"{gen_id}.json"
  meta_path.write_text(
      json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  record["metadata_gcs"] = GCS_MANAGER.upload_json(
      record, f"generations/{gen_id}.json"
  )
  pipeline_lat = round(time.time() - pipeline_t0, 2)
  log_runtime(
      "INFO",
      "GEN_PIPELINE_DONE",
      f"Generation pipeline completed for '{item_id}' (gen_id={gen_id}, duration={audio_res.get('duration_sec')}s, stage={audio_res.get('self_healing_stage_used')}) in {pipeline_lat}s",
      item_id=item_id,
      extra={
          "generation_id": gen_id,
          "item_id": item_id,
          "group": group_code,
          "duration_sec": audio_res.get("duration_sec"),
          "target_duration_sec": target_duration_sec,
          "self_healing_triggered": audio_res.get("self_healing_triggered"),
          "self_healing_stage_used": audio_res.get("self_healing_stage_used"),
          "total_pipeline_latency_sec": pipeline_lat,
      },
  )
  return record


def _persist_batch_job_state(
    job_id: str, upload_gcs: bool = True, force_flush: bool = False
):
  """Persists batch job state to local `/tmp` RAM cache, FUSE workspace, and GCS.

  - Serialized with `JOBS_PERSIST_LOCK` and rate-throttled on FUSE/GCS (at most 1 write per 1.2s
    unless `force_flush=True`) so concurrent worker threads NEVER trigger GCS FUSE
    `SetInodeAttributes: stale file handle / generation/metageneration mismatch`.
  """
  with JOBS_LOCK:
    if job_id not in JOBS_REGISTRY:
      return
    job_snapshot = json.loads(json.dumps(JOBS_REGISTRY[job_id]))

  serialized = json.dumps(job_snapshot, ensure_ascii=False, indent=2)

  with JOBS_PERSIST_LOCK:
    # 1. Always write to pure-RAM `/tmp` cache (immune to GCS FUSE stale handles)
    try:
      (LOCAL_TMP_JOBS_DIR / f"{job_id}.json").write_text(
          serialized, encoding="utf-8"
      )
    except Exception:
      pass

    now_ts = time.time()
    last_ts = _JOBS_LAST_PERSIST_TS.get(job_id, 0.0)
    is_terminal = job_snapshot.get("status") in ("completed", "failed")
    if not force_flush and not is_terminal and (now_ts - last_ts) < 1.2:
      return
    _JOBS_LAST_PERSIST_TS[job_id] = now_ts

    # 2. Upload live state to `batch_jobs/{job_id}.json` via GCS REST/SDK API for cross-instance polling
    if upload_gcs:
      try:
        GCS_MANAGER.upload_json(
            job_snapshot, f"batch_jobs/{job_id}.json", force=True
        )
      except Exception:
        pass

    # 3. Only write to JOBS_DIR (which on Cloud Run is mounted via GCS FUSE `/mnt/gcs/music-studio-workspace/jobs`)
    # ONCE when the batch job finishes (`is_terminal`). Never repeatedly overwrite an active file on GCS FUSE,
    # which otherwise triggers `SetInodeAttributes: stale file handle / generation/metageneration mismatch`.
    if is_terminal:
      job_file = JOBS_DIR / f"{job_id}.json"
      for _attempt in range(2):
        try:
          job_file.write_text(serialized, encoding="utf-8")
          break
        except OSError:
          time.sleep(0.1)
        except Exception:
          break


def _run_batch_job_worker(job_id: str, items: list[dict], options: dict):
  cfg = CONFIG_STORE.get()
  max_workers = max(
      1, min(20, int(options.get("max_workers") or cfg.get("batch_max_workers", 4)))
  )
  duration_strategy = (
      options.get("duration_strategy") or cfg.get("duration_strategy", "smart_auto")
  )
  batch_t0 = time.time()
  log_runtime(
      "INFO",
      "BATCH_JOB_START",
      f"Starting batch job '{job_id}' with {len(items)} items (workers={max_workers}, strategy={duration_strategy})",
      job_id=job_id,
      extra={
          "total_items": len(items),
          "max_workers": max_workers,
          "duration_strategy": duration_strategy,
          "options": options,
      },
  )

  def _process_one(idx_and_item):
    idx, it = idx_and_item
    item_id = it.get("id") or f"item_{idx}"
    item_t0 = time.time()
    with JOBS_LOCK:
      JOBS_REGISTRY[job_id]["items_status"][item_id] = {
          "status": "running",
          "started_at": datetime.now(timezone.utc).isoformat(),
      }
      JOBS_REGISTRY[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
    _persist_batch_job_state(job_id, upload_gcs=False, force_flush=False)
    log_runtime(
        "INFO",
        "BATCH_ITEM_START",
        f"[{job_id}] ({idx}/{len(items)}) Starting item '{item_id}'",
        job_id=job_id,
        item_id=item_id,
        extra={"index": idx, "total": len(items)},
    )
    try:
      grp = it.get("group", "A")
      mode = (
          "group_c_instrumental"
          if it.get("is_instrumental") or grp == "C"
          else ("group_b_prompt" if grp == "B" and not it.get("user_lyrics") else "group_a_lyrics")
      )
      if duration_strategy == "match_reference" and it.get("reference_duration_sec"):
        target_dur = float(it.get("reference_duration_sec") or 145.0)
      else:
        target_dur = float(
            it.get("target_duration_sec")
            or it.get("actual_duration_sec")
            or 145.0
        )
      req_payload = {
          "item_id": item_id,
          "mode": mode,
          "user_prompt": it.get("user_prompt", ""),
          "user_lyrics": it.get("user_lyrics", ""),
          "genre": it.get("genre", "Pop"),
          "raw_style": it.get("raw_style", ""),
          "language": it.get("language", "English"),
          "vocal_gender": it.get("vocal_gender", ""),
          "is_instrumental": bool(it.get("is_instrumental", 0)),
          "target_duration_sec": target_dur,
          "lyria_model": options.get("lyria_model") or cfg.get("lyria_default_model", "lyria-3-pro-preview"),
          "use_gemini_compiler": options.get("use_gemini_compiler", True),
          "compile_only": options.get("compile_only", False),
          "reference_audio_url": it.get("result_audio_url", ""),
      }
      res = execute_single_generation_pipeline(req_payload)
      aud_info = res.get("audio", {}) or {}
      with JOBS_LOCK:
        JOBS_REGISTRY[job_id]["completed"] += 1
        done_cnt = JOBS_REGISTRY[job_id]["completed"]
        JOBS_REGISTRY[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        JOBS_REGISTRY[job_id]["results"].append(res)
        JOBS_REGISTRY[job_id]["items_status"][item_id] = {
            "status": "completed",
            "generation_id": res.get("generation_id"),
            "audio_url": aud_info.get("audio_url", ""),
            "gcs_uri": aud_info.get("gcs", {}).get("gs_uri", ""),
            "probed_duration_sec": aud_info.get("duration_sec", 0.0),
            "self_healing_triggered": bool(aud_info.get("self_healing_triggered", False)),
            "self_healing_stage_used": aud_info.get("self_healing_stage_used", ""),
        }
      _persist_batch_job_state(job_id, upload_gcs=True, force_flush=False)
      log_runtime(
          "INFO",
          "BATCH_ITEM_DONE",
          f"[{job_id}] ({done_cnt}/{len(items)}) Completed '{item_id}' in {round(time.time() - item_t0, 2)}s (dur={aud_info.get('duration_sec')}s)",
          job_id=job_id,
          item_id=item_id,
          extra={
              "generation_id": res.get("generation_id"),
              "duration_sec": aud_info.get("duration_sec"),
              "latency_sec": round(time.time() - item_t0, 2),
          },
      )
    except Exception as e:
      with JOBS_LOCK:
        JOBS_REGISTRY[job_id]["failed"] += 1
        fail_cnt = JOBS_REGISTRY[job_id]["failed"]
        JOBS_REGISTRY[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        JOBS_REGISTRY[job_id]["items_status"][item_id] = {
            "status": "failed",
            "error": f"{type(e).__name__}: {e}",
        }
      _persist_batch_job_state(job_id, upload_gcs=True, force_flush=False)
      log_runtime(
          "ERROR",
          "BATCH_ITEM_ERROR",
          f"[{job_id}] Item '{item_id}' failed ({type(e).__name__}: {e}) [failed={fail_cnt}]",
          job_id=job_id,
          item_id=item_id,
          exc=e,
      )

  with ThreadPoolExecutor(max_workers=max_workers) as pool:
    list(pool.map(_process_one, enumerate(items, start=1)))

  with JOBS_LOCK:
    JOBS_REGISTRY[job_id]["status"] = "completed"
    JOBS_REGISTRY[job_id]["finished_at"] = datetime.now(timezone.utc).isoformat()
    JOBS_REGISTRY[job_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
    comp_total = JOBS_REGISTRY[job_id]["completed"]
    fail_total = JOBS_REGISTRY[job_id]["failed"]

  _persist_batch_job_state(job_id, upload_gcs=True, force_flush=True)
  try:
    CostAnalyticsEngine.save_and_archive_cost_snapshot(
        group="ALL", scope="all_dataset", trigger=f"auto_batch_{job_id}"
    )
  except Exception:
    pass
  log_runtime(
      "INFO",
      "BATCH_JOB_DONE",
      f"Batch job '{job_id}' finished in {round(time.time() - batch_t0, 2)}s: completed={comp_total}/{len(items)}, failed={fail_total}",
      job_id=job_id,
      extra={
          "completed": comp_total,
          "failed": fail_total,
          "total": len(items),
          "latency_sec": round(time.time() - batch_t0, 2),
      },
  )


# ==============================================================================
# 10. FastAPI Application & Zero-Plaintext SHA-256 Passcode Gatekeeper
# ==============================================================================
app = fastapi.FastAPI(
    title=STUDIO_APP_NAME_EN,
    version="2.1.0",
    description=(
        "MuseWave Studio — Music Creation, Deep Acoustic Intelligence & Multi-Track Evaluation Arena "
        "powered by Gemini (`gemini-4-argon`, thinking_level=medium) & Lyria 3."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def http_observability_middleware(request: Request, call_next):
  """Correlates Cloud Run `X-Cloud-Trace-Context` with structured application logs and records HTTP API latency & errors."""
  t0 = time.monotonic()
  path = request.url.path
  method = request.method

  # Parse Cloud Run `X-Cloud-Trace-Context: TRACE_ID/SPAN_ID;o=1`
  trace_header = request.headers.get("X-Cloud-Trace-Context", "")
  trace_id = ""
  span_id = ""
  cloud_trace = ""
  if trace_header:
    parts = trace_header.split(";")[0].split("/")
    trace_id = parts[0].strip()
    if len(parts) > 1:
      span_id = parts[1].strip()
    proj_id = CONFIG_STORE.get().get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if trace_id and proj_id and proj_id != "your-gcp-project-id":
      cloud_trace = f"projects/{proj_id}/traces/{trace_id}"

  token = _TRACE_CONTEXT_VAR.set({
      "trace_id": trace_id,
      "span_id": span_id,
      "cloud_trace": cloud_trace,
  })
  is_tracked_api = path.startswith("/api/") and path not in (
      "/api/health",
      "/api/logs",
      "/api/generations/index",
  )
  if is_tracked_api and method in ("POST", "PUT", "DELETE"):
    log_runtime(
        "INFO",
        "HTTP_API_START",
        f"--> {method} {path}",
        extra={
            "method": method,
            "path": path,
            "query": str(request.url.query or ""),
            "client": request.client.host if request.client else "",
        },
    )

  try:
    resp = await call_next(request)
  except Exception as exc:
    dur_ms = round((time.monotonic() - t0) * 1000.0, 1)
    log_runtime(
        "ERROR",
        "HTTP_UNHANDLED_EXC",
        f"<-- {method} {path} raised unhandled {type(exc).__name__}: {exc} ({dur_ms}ms)",
        extra={"method": method, "path": path, "duration_ms": dur_ms},
        exc=exc,
    )
    _TRACE_CONTEXT_VAR.reset(token)
    raise

  dur_ms = round((time.monotonic() - t0) * 1000.0, 1)
  if is_tracked_api or resp.status_code >= 400:
    lvl = (
        "ERROR"
        if resp.status_code >= 500
        else ("WARNING" if resp.status_code >= 400 else "INFO")
    )
    log_runtime(
        lvl,
        "HTTP_API_DONE",
        f"<-- {method} {path} -> {resp.status_code} ({dur_ms}ms)",
        extra={
            "method": method,
            "path": path,
            "status_code": resp.status_code,
            "duration_ms": dur_ms,
        },
    )
  _TRACE_CONTEXT_VAR.reset(token)
  return resp


# Passcode Gatekeeper Configuration (100% compatible with MV Director Studio)
_RAW_PASSCODE_ENV = (
    os.environ.get("MUSIC_STUDIO_ACCESS_PASSCODE")
    or os.environ.get("VIRAL_PITCH_ACCESS_PASSCODE")
    or ""
).strip()
_HASH_PASSCODE_ENV = (
    os.environ.get("MUSIC_STUDIO_PASSCODE_SHA256")
    or os.environ.get("VIRAL_PITCH_PASSCODE_SHA256")
    or ""
).strip().lower()

if _HASH_PASSCODE_ENV:
  EXPECTED_PASSCODE_SHA256 = _HASH_PASSCODE_ENV
elif _RAW_PASSCODE_ENV:
  EXPECTED_PASSCODE_SHA256 = hashlib.sha256(
      _RAW_PASSCODE_ENV.encode("utf-8")
  ).hexdigest().lower()
else:
  EXPECTED_PASSCODE_SHA256 = ""

PASSCODE_COOKIE_NAME = "music_studio_passcode_sha256"

GATEKEEPER_LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>🔒 安全访问验证 — MuseWave Studio · 全景音乐创作与声学洞察</title>
  <link rel="icon" type="image/svg+xml" href="/icon.svg" />
  <style>
    :root { --bg: #090b12; --card: #121726; --border: #232c47; --accent: #6366f1; --accent2: #00d4ff; --text: #f1f5f9; --muted: #94a3b8; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: radial-gradient(circle at 20% 20%, #1a163b 0%, #090b12 65%); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .login-card { width: 100%; max-width: 440px; background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 34px 30px; box-shadow: 0 24px 60px rgba(0,0,0,0.65); }
    .brand-row { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
    .brand-icon { width: 42px; height: 42px; border-radius: 11px; overflow: hidden; box-shadow: 0 6px 18px rgba(99,102,241,0.42); flex-shrink: 0; }
    .brand-icon img { width: 100%; height: 100%; display: block; }
    .badge { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; font-weight: 600; color: var(--accent2); background: rgba(0,212,255,0.1); border: 1px solid rgba(0,212,255,0.28); border-radius: 999px; padding: 3px 10px; margin-bottom: 4px; }
    h1 { margin: 0; font-size: 19px; font-weight: 800; letter-spacing: -0.01em; }
    p { margin: 0 0 20px; font-size: 13px; color: var(--muted); line-height: 1.55; }
    input[type="password"] { width: 100%; padding: 12px 14px; border-radius: 10px; border: 1px solid var(--border); background: #0b0e18; color: var(--text); font-size: 15px; outline: none; margin-bottom: 14px; }
    input[type="password"]:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(99,102,241,0.25); }
    button { width: 100%; padding: 12px; border: none; border-radius: 10px; background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff; font-weight: 600; font-size: 14px; cursor: pointer; transition: opacity 0.15s; }
    button:hover { opacity: 0.92; }
    .err { color: #f87171; font-size: 13px; margin-top: 12px; display: none; }
  </style>
</head>
<body>
  <div class="login-card">
    <div class="brand-row">
      <div class="brand-icon"><img src="/icon.svg" alt="MuseWave Studio" /></div>
      <div>
        <div class="badge">✨ Music Creation & Acoustic Intelligence</div>
        <h1>MuseWave Studio · 全景音乐创作与声学洞察</h1>
      </div>
    </div>
    <p>当前环境已启用零明文 SHA-256 访问门禁保护，请输入访问口令进入工作台：</p>
    <form id="loginForm">
      <input type="password" id="passInput" placeholder="请输入访问口令 (Access Passcode)..." autocomplete="current-password" required autofocus />
      <button type="submit">🔐 验证并进入 MuseWave Studio →</button>
      <div class="err" id="errMsg">访问口令不正确，请重试。</div>
    </form>
  </div>
  <script>
    async function sha256Hex(str) {
      const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
      return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, "0")).join("");
    }
    document.getElementById("loginForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const raw = document.getElementById("passInput").value.trim();
      if (!raw) return;
      const hash = (raw.length === 64 && /^[0-9a-fA-F]+$/.test(raw)) ? raw.toLowerCase() : await sha256Hex(raw);
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({passcode_sha256: hash})
      });
      if (res.ok) {
        window.location.href = "/";
      } else {
        document.getElementById("errMsg").style.display = "block";
      }
    });
  </script>
</body>
</html>"""


def _verify_hash(candidate: str) -> bool:
  if not EXPECTED_PASSCODE_SHA256:
    return True
  c = (candidate or "").strip().lower()
  if not c:
    return False
  # Allow either raw passcode or SHA-256 hex digest
  if len(c) == 64 and all(ch in "0123456789abcdef" for ch in c):
    return hmac.compare_digest(c, EXPECTED_PASSCODE_SHA256)
  hashed = hashlib.sha256(candidate.strip().encode("utf-8")).hexdigest().lower()
  return hmac.compare_digest(hashed, EXPECTED_PASSCODE_SHA256)


@app.middleware("http")
async def passcode_gatekeeper_middleware(request: Request, call_next):
  if not EXPECTED_PASSCODE_SHA256:
    return await call_next(request)

  path = request.url.path
  if path in (
      "/api/health",
      "/api/auth/login",
      "/api/auth/logout",
      "/icon.svg",
      "/favicon.ico",
  ):
    return await call_next(request)

  # Check URL `?token=` or `?passcode=` (sets cookie and 303 redirects to clean URL)
  q_tok = request.query_params.get("token") or request.query_params.get("passcode")
  if q_tok and _verify_hash(q_tok):
    digest = (
        q_tok.lower()
        if len(q_tok) == 64
        else hashlib.sha256(q_tok.encode("utf-8")).hexdigest().lower()
    )
    resp = RedirectResponse(url=path, status_code=303)
    resp.set_cookie(
        key=PASSCODE_COOKIE_NAME,
        value=digest,
        max_age=86400 * 14,
        httponly=True,
        samesite="lax",
    )
    return resp

  cookie_val = request.cookies.get(PASSCODE_COOKIE_NAME, "")
  header_val = request.headers.get("X-Passcode-SHA256", "")
  if _verify_hash(cookie_val) or _verify_hash(header_val):
    return await call_next(request)

  if path.startswith("/api/"):
    return JSONResponse(
        status_code=401,
        content={"error": "Unauthorized: valid passcode required."},
    )
  return HTMLResponse(content=GATEKEEPER_LOGIN_HTML, status_code=401)


@app.post("/api/auth/login")
async def api_auth_login(payload: dict = Body(...)):
  cand = payload.get("passcode_sha256") or payload.get("passcode") or ""
  if not _verify_hash(cand):
    raise HTTPException(status_code=401, detail="Invalid passcode")
  digest = (
      cand.lower()
      if len(cand) == 64
      else hashlib.sha256(cand.encode("utf-8")).hexdigest().lower()
  )
  resp = JSONResponse({"ok": True})
  resp.set_cookie(
      key=PASSCODE_COOKIE_NAME,
      value=digest,
      max_age=86400 * 14,
      httponly=True,
      samesite="lax",
  )
  return resp


@app.post("/api/auth/logout")
async def api_auth_logout():
  resp = JSONResponse({"ok": True})
  resp.delete_cookie(PASSCODE_COOKIE_NAME)
  return resp


# ==============================================================================
# 11. Studio REST API Endpoints
# ==============================================================================
@app.get("/icon.svg")
async def serve_brand_icon_svg():
  return Response(content=STUDIO_ICON_SVG, media_type="image/svg+xml")


@app.get("/favicon.ico")
async def serve_favicon():
  return Response(content=STUDIO_ICON_SVG, media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
  ui_path = BASE_DIR / "music_studio_ui.html"
  if ui_path.exists():
    return HTMLResponse(
        ui_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
  return HTMLResponse("<h1>MuseWave Studio UI file not found</h1>", status_code=404)


@app.get("/api/logs")
async def api_get_runtime_logs(
    limit: int = 200,
    level: str = "",
    stage: str = "",
    item_id: str = "",
    job_id: str = "",
):
  """Returns structured runtime logs for in-app observability and debugging."""
  logs = get_runtime_logs(
      limit=limit, level=level, stage=stage, item_id=item_id, job_id=job_id
  )
  return {"ok": True, "count": len(logs), "logs": logs}


@app.post("/api/logs/clear")
async def api_clear_runtime_logs():
  cleared = clear_runtime_logs()
  return {"ok": True, "cleared": cleared}


@app.get("/api/health")
async def api_health():
  cfg = CONFIG_STORE.get()
  return {
      "ok": True,
      "service": "music-studio",
      "gemini_model": cfg.get("gemini_model"),
      "gemini_thinking_level": cfg.get("gemini_thinking_level"),
      "lyria_default_model": cfg.get("lyria_default_model"),
      "gcs_bucket": GCS_MANAGER.bucket_name,
      "dataset_loaded_count": BUILTIN_DATASET.get("total_count", 0),
      "passcode_protected": bool(EXPECTED_PASSCODE_SHA256),
  }


@app.get("/api/config")
async def api_get_config():
  return CONFIG_STORE.get()


@app.post("/api/config")
async def api_update_config(patch: dict = Body(...)):
  updated = CONFIG_STORE.update(patch)
  return {"ok": True, "config": updated}


@app.post("/api/config/reset")
async def api_reset_config():
  cfg = CONFIG_STORE.reset()
  return {"ok": True, "config": cfg}


@app.get("/api/dataset")
async def api_get_dataset(
    group: str = "",
    language: str = "",
    genre: str = "",
    lyria_supported: str = "",
    q: str = "",
    auto_gcs: bool = True,
):
  """Returns the active Studio dataset (auto-loaded from GCS or uploaded prompt lists) with optional filtering."""
  if (
      auto_gcs
      and not BUILTIN_DATASET.get("items")
      and GCS_MANAGER.bucket_name
      and GCS_MANAGER.bucket_name not in ("your-gcs-bucket-name", "")
  ):
    try:
      await asyncio.to_thread(
          GCS_MANAGER.discover_assets,
          scan_entire_bucket_if_empty=True,
          auto_register=True,
          auto_load_dataset=True,
      )
    except Exception as e:
      print(f"[Dataset] Initial GCS dataset auto-load skipped: {e}")

  items = BUILTIN_DATASET.get("items", [])
  filtered = []
  for it in items:
    if group and it.get("group") != group:
      continue
    if language and it.get("language") != language:
      continue
    if genre and it.get("genre") != genre:
      continue
    if lyria_supported and it.get("lyria_official_lang_supported") != lyria_supported:
      continue
    if q:
      ql = q.lower()
      haystack = " ".join([
          str(it.get("id", "")),
          str(it.get("user_prompt", "")),
          str(it.get("user_lyrics", "")),
          str(it.get("genre", "")),
          str(it.get("raw_style", "")),
          str(it.get("language", "")),
          str(it.get("country", "")),
      ]).lower()
      if ql not in haystack:
        continue
    filtered.append(it)

  return {
      "version": BUILTIN_DATASET.get("version"),
      "source_folder": BUILTIN_DATASET.get("source_folder"),
      "counts": BUILTIN_DATASET.get("counts"),
      "files_analyzed": BUILTIN_DATASET.get("files_analyzed"),
      "filters": BUILTIN_DATASET.get("filters"),
      "spec_table": BUILTIN_DATASET.get("spec_table"),
      "stats_table": BUILTIN_DATASET.get("stats_table"),
      "filtered_count": len(filtered),
      "items": filtered,
  }


@app.post("/api/prompt-list/upload")
async def api_upload_prompt_list(
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    target_group: str = Form(default="AUTO"),
    load_mode: str = Form(default="merge_builtin"),
    gcs_bucket: str = Form(default=""),
):
  """Uploads one or multiple A/B/C group Prompt Lists (CSV / XLSX / JSON / TXT) to GCS,

  automatically parses & classifies them into Group A / B / C, and updates `BUILTIN_DATASET`
  so the items immediately appear in Tab 1 (Group A), Tab 2 (Group B), Tab 3 (Group C), and Tab 4.
  """
  upload_list: list[UploadFile] = []
  if files:
    upload_list.extend([f for f in files if f and f.filename])
  if file and file.filename and file not in upload_list:
    upload_list.append(file)
  if not upload_list:
    raise HTTPException(status_code=400, detail="Please select at least one CSV/XLSX/JSON/TXT file to upload.")

  if gcs_bucket and gcs_bucket.strip():
    CONFIG_STORE.update({"gcs_bucket_name": gcs_bucket.strip()})

  all_items: list[dict] = []
  uploaded_filenames: list[str] = []
  gcs_uploaded_files: list[dict] = []
  tg = (target_group or "AUTO").strip().upper()
  mode_clean = (load_mode or "merge_builtin").strip().lower()

  for up_file in upload_list:
    raw_bytes = await up_file.read()
    fname = up_file.filename or "uploaded_prompts.csv"
    uploaded_filenames.append(fname)
    clean_fname = re.sub(r"[^a-zA-Z0-9._\u4e00-\u9fa5-]", "_", fname)
    safe_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{clean_fname}"
    local_path = UPLOADS_LISTS_DIR / safe_name
    local_path.write_bytes(raw_bytes)

    try:
      parsed = parse_uploaded_prompt_list(fname, raw_bytes, target_group=tg)
    except Exception as e:
      raise HTTPException(
          status_code=400, detail=f"Failed to parse prompt list ({fname}): {e}"
      ) from e

    # Archive both to `datasets/<clean_fname>` (for GCS auto-discovery) and `uploads/prompt_lists/<safe_name>`
    gcs_ds = await asyncio.to_thread(
        GCS_MANAGER.upload_file, local_path, f"datasets/{clean_fname}"
    )
    gcs_raw = await asyncio.to_thread(
        GCS_MANAGER.upload_file, local_path, f"uploads/prompt_lists/{safe_name}"
    )
    gcs_uploaded_files.append({
        "filename": fname,
        "count": len(parsed),
        "gcs_dataset": gcs_ds,
        "gcs_raw": gcs_raw,
    })
    all_items.extend(parsed)

  list_id = f"plist_{uuid.uuid4().hex[:8]}"
  parsed_path = UPLOADS_LISTS_DIR / f"{list_id}.json"
  source_label = ", ".join(uploaded_filenames)
  if GCS_MANAGER.bucket_name:
    source_label = f"gs://{GCS_MANAGER.bucket_name}/{GCS_MANAGER.prefix}/datasets ({', '.join(uploaded_filenames)})"

  # Update active BUILTIN_DATASET so Group A, Group B, Group C workbenches & Tab 4 render immediately
  if mode_clean in ("merge_builtin", "replace_builtin", "builtin"):
    _apply_items_to_builtin_dataset(
        list(all_items),
        source_label=source_label,
        merge_existing=(mode_clean == "merge_builtin"),
    )
    try:
      await asyncio.to_thread(
          GCS_MANAGER.upload_json,
          BUILTIN_DATASET,
          "datasets/active_dataset_snapshot.json",
      )
    except Exception:
      pass

  record = {
      "list_id": list_id,
      "original_filename": ", ".join(uploaded_filenames),
      "uploaded_at": datetime.now(timezone.utc).isoformat(),
      "target_group": tg,
      "load_mode": mode_clean,
      "count": len(all_items),
      "active_dataset_total": BUILTIN_DATASET.get("total_count", len(all_items)),
      "group_counts": BUILTIN_DATASET.get("counts", {}),
      "items": all_items,
      "gcs_files": gcs_uploaded_files,
      "gcs_datasets": [x["gcs_dataset"] for x in gcs_uploaded_files if x.get("gcs_dataset")],
      "gcs_raw": gcs_uploaded_files[0]["gcs_dataset"] if gcs_uploaded_files else {"uploaded": False},
  }
  parsed_path.write_text(
      json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  record["gcs_parsed"] = await asyncio.to_thread(
      GCS_MANAGER.upload_json, record, f"uploads/prompt_lists/{list_id}.json"
  )
  return record


@app.get("/api/prompt-list/history")
async def api_list_uploaded_prompt_lists():
  lists = []
  for p in sorted(UPLOADS_LISTS_DIR.glob("plist_*.json"), reverse=True):
    try:
      d = json.loads(p.read_text(encoding="utf-8"))
      lists.append({
          "list_id": d.get("list_id"),
          "original_filename": d.get("original_filename"),
          "uploaded_at": d.get("uploaded_at"),
          "count": d.get("count", 0),
          "items": d.get("items", []),
      })
    except Exception:
      pass
  return {"items": lists}


@app.post("/api/audio/upload")
async def api_upload_audio(
    file: UploadFile = File(...),
    gcs_bucket: str = Form(default=""),
):
  """Uploads an audio file (MP3/WAV/FLAC/OGG/M4A), probes its metadata, and stores it in GCS."""
  if gcs_bucket and gcs_bucket.strip():
    clean_b = gcs_bucket.strip().replace("gs://", "").strip("/").split("/")[0]
    if clean_b and clean_b != "your-gcs-bucket-name":
      CONFIG_STORE.update({"gcs_bucket_name": clean_b})
  raw_bytes = await file.read()
  fname = file.filename or "audio.mp3"
  ext = Path(fname).suffix.lower() or ".mp3"
  track_id = f"up_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
  safe_fname = f"{track_id}{ext}"
  local_path = UPLOADS_AUDIO_DIR / safe_fname
  local_path.write_bytes(raw_bytes)

  probe = probe_audio_file(local_path)
  mime = _guess_audio_mime(local_path)
  gcs_res = GCS_MANAGER.upload_file(
      local_path, f"uploads/audio/{safe_fname}", content_type=mime
  )

  record = {
      "upload_id": track_id,
      "original_filename": fname,
      "filename": safe_fname,
      "audio_url": f"/api/audio/uploads/{safe_fname}",
      "local_path": str(local_path),
      "mime_type": mime,
      "probe": probe,
      "gcs": gcs_res,
      "uploaded_at": datetime.now(timezone.utc).isoformat(),
  }
  (UPLOADS_AUDIO_DIR / f"{track_id}.json").write_text(
      json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  return record


@app.get("/api/audio/{category}/{filename}")
async def api_serve_audio(category: str, filename: str):
  folder = GENERATED_DIR if category == "generated" else UPLOADS_AUDIO_DIR
  safe_fname = Path(filename).name
  target = (folder / safe_fname).resolve()
  if not target.exists():
    # Automatically retrieve from GCS bucket if missing locally (e.g., after Cloud Run restart)
    rel_gcs = (
        f"generations/{safe_fname}"
        if category == "generated"
        else f"uploads/audio/{safe_fname}"
    )
    await asyncio.to_thread(GCS_MANAGER.download_file, rel_gcs, target)
  if not target.exists():
    raise HTTPException(status_code=404, detail="Audio file not found locally or in GCS")
  return FileResponse(target, media_type=_guess_audio_mime(target))


@app.post("/api/lyria/compile-plan")
async def api_compile_lyria_song_plan(payload: dict = Body(...)):
  """Compiles a request into a Lyria 3 Multi-Caption & SongPlan via `gemini-4-argon` (`thinking_level=medium`) without generating audio yet."""
  payload["compile_only"] = True
  item_id = payload.get("item_id") or "custom"
  try:
    ctx = contextvars.copy_context()
    return await asyncio.to_thread(
        ctx.run, execute_single_generation_pipeline, payload
    )
  except Exception as e:
    log_runtime(
        "ERROR",
        "API_COMPILE_PLAN_ERROR",
        f"/api/lyria/compile-plan failed for '{item_id}': {type(e).__name__}: {e}",
        item_id=str(item_id),
        exc=e,
    )
    raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@app.post("/api/generate/single")
async def api_generate_single_music(payload: dict = Body(...)):
  """Generates 1..4 parallel music tracks using Gemini SongPlan Compiler + Lyria and archives to GCS."""
  item_id = payload.get("item_id") or "custom"
  try:
    ctx = contextvars.copy_context()
    return await asyncio.to_thread(
        ctx.run, execute_single_generation_pipeline, payload
    )
  except Exception as e:
    log_runtime(
        "ERROR",
        "API_GENERATE_SINGLE_ERROR",
        f"/api/generate/single failed for '{item_id}': {type(e).__name__}: {e}",
        item_id=str(item_id),
        extra={
            "mode": payload.get("mode"),
            "genre": payload.get("genre"),
            "lyria_model": payload.get("lyria_model"),
            "target_duration_sec": payload.get("target_duration_sec"),
        },
        exc=e,
    )
    raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@app.post("/api/generate/batch")
async def api_generate_batch_music(payload: dict = Body(...)):
  """Launches a concurrent batch music generation job from selected items, a specific group (A/B/C/ALL), or an uploaded prompt list."""
  items = payload.get("items") or []
  if not items:
    item_ids = set(payload.get("item_ids") or [])
    group = (payload.get("group") or "").strip()
    limit = int(payload.get("limit") or 500)
    pool = BUILTIN_DATASET.get("items", [])
    if item_ids:
      items = [x for x in pool if x.get("id") in item_ids]
    elif group.upper() == "ALL":
      items = list(pool)[:limit]
    elif group:
      items = [x for x in pool if x.get("group") == group][:limit]

  if not items:
    raise HTTPException(status_code=400, detail="No items selected for batch generation")

  job_id = f"batch_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
  group_tag = (payload.get("group") or "CUSTOM").strip().upper()
  options = {
      "group": group_tag,
      "lyria_model": payload.get("lyria_model"),
      "duration_strategy": payload.get("duration_strategy"),
      "use_gemini_compiler": payload.get("use_gemini_compiler", True),
      "compile_only": payload.get("compile_only", False),
      "max_workers": payload.get("max_workers", 4),
  }

  with JOBS_LOCK:
    JOBS_REGISTRY[job_id] = {
        "job_id": job_id,
        "group": group_tag,
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "completed": 0,
        "failed": 0,
        "options": options,
        "items_status": {
            (it.get("id") or f"item_{i}"): {"status": "queued"}
            for i, it in enumerate(items, start=1)
        },
        "results": [],
    }

  # Persist immediately before returning so any Cloud Run instance can serve `/api/jobs/{job_id}`
  await asyncio.to_thread(_persist_batch_job_state, job_id, True, True)

  t = threading.Thread(
      target=_run_batch_job_worker,
      args=(job_id, items, options),
      daemon=True,
  )
  t.start()
  return {"ok": True, "job_id": job_id, "group": group_tag, "total": len(items)}


def _build_generations_index() -> dict:
  """Builds a per-item_id history map (`by_item`) and per-group summary of all generated tracks & SongPlans."""
  by_item: dict[str, list[dict]] = {}
  all_generations: list[dict] = []
  cfg = CONFIG_STORE.get()
  analyses_lookup = CostAnalyticsEngine._load_analyses_lookup()
  for p in sorted(
      GENERATED_DIR.glob("gen_*.json"),
      key=lambda x: x.stat().st_mtime,
      reverse=True,
  ):
    try:
      rec = json.loads(p.read_text(encoding="utf-8"))
      rec["cost_breakdown"] = CostAnalyticsEngine.compute_single_generation_cost(
          rec, analyses_lookup=analyses_lookup, cfg=cfg
      )
      all_generations.append(rec)
      iid = rec.get("item_id") or "custom"
      by_item.setdefault(iid, []).append(rec)
      if re.match(r"^[ABC]\d{2,3}$", iid, re.IGNORECASE):
        prefixed = f"{iid[0].upper()}_{iid.upper()}"
        by_item.setdefault(prefixed, []).append(rec)
      elif re.match(r"^[ABC]_[ABC]\d{2,3}$", iid, re.IGNORECASE):
        short_id = iid.split("_", 1)[1].upper()
        by_item.setdefault(short_id, []).append(rec)
    except Exception:
      pass

  # Sort each item's records newest-first by created_at
  for iid, recs in by_item.items():
    recs.sort(key=lambda r: r.get("created_at") or "", reverse=True)

  # Collect active & recent batch jobs from both FUSE archive and local `/tmp` RAM cache
  jobs_map: dict[str, dict] = {}
  for j_dir in [JOBS_DIR, LOCAL_TMP_JOBS_DIR]:
    try:
      for jf in sorted(j_dir.glob("batch_*.json"), reverse=True)[:25]:
        try:
          jd = json.loads(jf.read_text(encoding="utf-8"))
          if jd.get("job_id"):
            jobs_map[jd["job_id"]] = jd
        except Exception:
          pass
    except Exception:
      pass
  with JOBS_LOCK:
    for jid, jd in JOBS_REGISTRY.items():
      jobs_map[jid] = jd

  return {
      "total_records": len(all_generations),
      "total_items_with_history": len(by_item),
      "by_item": by_item,
      "recent_generations": all_generations[:150],
      "batch_jobs": list(jobs_map.values()),
  }


@app.get("/api/generations/index")
async def api_get_generations_index(sync_gcs: bool = False):
  """Returns the complete per-item generation history (`by_item`) and batch job statuses.

  If `sync_gcs=True`, first pulls any missing metadata JSONs from the configured GCS bucket.
  """
  gcs_sync_info = None
  if sync_gcs:
    gcs_sync_info = await asyncio.to_thread(GCS_MANAGER.sync_metadata_from_gcs)
  idx_data = await asyncio.to_thread(_build_generations_index)
  idx_data["gcs_sync"] = gcs_sync_info
  return idx_data


@app.get("/api/generations/by-item/{item_id}")
async def api_get_generations_by_item(item_id: str, sync_gcs: bool = False):
  """Retrieves all historical generated tracks and compiled SongPlans for a specific `item_id` (e.g., `A01`, `B05`, `C12`)."""
  if sync_gcs:
    await asyncio.to_thread(GCS_MANAGER.sync_metadata_from_gcs)
  idx_data = await asyncio.to_thread(_build_generations_index)
  records = idx_data["by_item"].get(item_id, [])
  return {
      "item_id": item_id,
      "count": len(records),
      "records": records,
  }


@app.post("/api/library/reload")
async def api_reload_studio_library(payload: dict = Body(default={})):
  """Synchronizes all historical generations, uploaded prompt lists, uploaded audio, and evaluations from GCS + local workspace."""
  sync_gcs = bool(payload.get("sync_gcs", True))
  gcs_sync_info = None
  if sync_gcs:
    gcs_sync_info = await asyncio.to_thread(GCS_MANAGER.sync_metadata_from_gcs)
  idx_data = await asyncio.to_thread(_build_generations_index)
  idx_data["gcs_sync"] = gcs_sync_info
  return idx_data


@app.get("/api/jobs/{job_id}")
async def api_get_batch_job(job_id: str):
  with JOBS_LOCK:
    if job_id in JOBS_REGISTRY:
      return JOBS_REGISTRY[job_id]

  safe_job_id = Path(job_id).name

  # 1. Check pure-RAM `/tmp` cache on this instance
  tmp_jf = LOCAL_TMP_JOBS_DIR / f"{safe_job_id}.json"
  cached_job = None
  if tmp_jf.exists():
    try:
      cached_job = json.loads(tmp_jf.read_text(encoding="utf-8"))
      if cached_job.get("status") == "completed":
        return cached_job
    except Exception:
      pass

  # 2. Read-only check on JOBS_DIR (may be a GCS FUSE mount; never write/clobber here during GET!)
  jf = JOBS_DIR / f"{safe_job_id}.json"
  for _attempt in range(2):
    try:
      if jf.exists():
        fuse_job = json.loads(jf.read_text(encoding="utf-8"))
        if fuse_job:
          cached_job = fuse_job
          if fuse_job.get("status") == "completed":
            return fuse_job
      break
    except OSError:
      await asyncio.sleep(0.05)
    except Exception:
      break

  # 3. Pull latest snapshot directly into memory from GCS (never overwrites FUSE `jf`!)
  gcs_job = await asyncio.to_thread(
      GCS_MANAGER.download_json, f"batch_jobs/{safe_job_id}.json"
  )
  if isinstance(gcs_job, dict) and gcs_job.get("job_id"):
    try:
      tmp_jf.write_text(
          json.dumps(gcs_job, ensure_ascii=False, indent=2), encoding="utf-8"
      )
    except Exception:
      pass
    return gcs_job

  if cached_job is not None:
    return cached_job

  raise HTTPException(status_code=404, detail="Job not found")


@app.get("/api/library")
async def api_get_studio_library():
  """Lists all generated tracks, uploaded audio files, music analyses, and multi-track evaluations."""
  generations = []
  for p in sorted(GENERATED_DIR.glob("gen_*.json"), reverse=True)[:100]:
    try:
      generations.append(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
      pass

  uploads = []
  for p in sorted(UPLOADS_AUDIO_DIR.glob("up_*.json"), reverse=True)[:100]:
    try:
      uploads.append(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
      pass

  analyses = []
  for p in sorted(ANALYSES_DIR.glob("ana_*.json"), reverse=True)[:50]:
    try:
      analyses.append(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
      pass

  evaluations = []
  for p in sorted(EVALUATIONS_DIR.glob("eval_*.json"), reverse=True)[:50]:
    try:
      evaluations.append(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
      pass

  return {
      "generations": generations,
      "uploads": uploads,
      "analyses": analyses,
      "evaluations": evaluations,
  }


@app.post("/api/analyze/audio")
async def api_analyze_audio(payload: dict = Body(...)):
  """Runs deep music analysis via `gemini-4-argon` (`thinking_level="MEDIUM"`)."""
  audio_source = payload.get("audio_source") or payload.get("audio_url") or ""
  if not audio_source:
    raise HTTPException(status_code=400, detail="audio_source is required")
  try:
    ctx = contextvars.copy_context()
    return await asyncio.to_thread(
        ctx.run,
        run_deep_music_analysis,
        audio_source=audio_source,
        track_title_hint=payload.get("track_title_hint", ""),
        reference_lyrics=payload.get("reference_lyrics", ""),
        reference_prompt=payload.get("reference_prompt", ""),
        custom_system_prompt=payload.get("custom_system_prompt"),
    )
  except Exception as e:
    log_runtime(
        "ERROR",
        "API_ANALYZE_AUDIO_ERROR",
        f"/api/analyze/audio failed for '{audio_source}': {type(e).__name__}: {e}",
        extra={"audio_source": audio_source},
        exc=e,
    )
    raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@app.post("/api/analyze/upload-and-run")
async def api_upload_and_analyze_audio(
    file: UploadFile = File(...),
    track_title_hint: str = Form(default=""),
    reference_prompt: str = Form(default=""),
    reference_lyrics: str = Form(default=""),
    gcs_bucket: str = Form(default=""),
):
  """Uploads an audio file (MP3/WAV/FLAC/OGG/M4A), archives it to GCS, and immediately runs deep music analysis."""
  up_record = await api_upload_audio(file=file, gcs_bucket=gcs_bucket)
  try:
    ctx = contextvars.copy_context()
    ana_record = await asyncio.to_thread(
        ctx.run,
        run_deep_music_analysis,
        audio_source=up_record["audio_url"],
        track_title_hint=track_title_hint or up_record.get("original_filename", ""),
        reference_lyrics=reference_lyrics,
        reference_prompt=reference_prompt,
    )
    ana_record["upload"] = up_record
    ana_record["uploaded_audio"] = up_record
    return ana_record
  except Exception as e:
    log_runtime(
        "ERROR",
        "API_UPLOAD_AND_ANALYZE_ERROR",
        f"/api/analyze/upload-and-run failed for '{up_record.get('original_filename')}': {type(e).__name__}: {e}",
        exc=e,
    )
    raise HTTPException(status_code=500, detail=f"Audio uploaded ({up_record['audio_url']}) but analysis failed: {e}") from e


@app.post("/api/evaluate/compare")
async def api_evaluate_compare(payload: dict = Body(...)):
  """Runs multi-track evaluation & comparison via `gemini-4-argon` (`thinking_level="MEDIUM"`)."""
  tracks = payload.get("tracks") or []
  try:
    ctx = contextvars.copy_context()
    return await asyncio.to_thread(
        ctx.run,
        run_multi_track_evaluation,
        tracks=tracks,
        target_prompt=payload.get("target_prompt", ""),
        target_lyrics=payload.get("target_lyrics", ""),
        target_genre=payload.get("target_genre", ""),
        target_language=payload.get("target_language", ""),
        target_vocal_gender=payload.get("target_vocal_gender", ""),
        is_instrumental=bool(payload.get("is_instrumental", False)),
        blind_mode=bool(payload.get("blind_mode", True)),
        custom_system_prompt=payload.get("custom_system_prompt"),
    )
  except Exception as e:
    log_runtime(
        "ERROR",
        "API_EVALUATE_COMPARE_ERROR",
        f"/api/evaluate/compare failed ({len(tracks)} tracks): {type(e).__name__}: {e}",
        extra={"tracks_count": len(tracks)},
        exc=e,
    )
    raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}") from e


@app.get("/api/gcs/list")
async def api_gcs_list(
    sub_prefix: str = "",
    bucket: str = "",
    raw_prefix: str | None = None,
    max_results: int = 250,
):
  return await asyncio.to_thread(
      GCS_MANAGER.list_objects,
      sub_prefix=sub_prefix,
      max_results=max_results,
      bucket_override=bucket,
      raw_prefix=raw_prefix,
  )


@app.get("/api/gcs/stream")
async def api_gcs_stream(uri: str = ""):
  """Streams any `gs://<bucket>/<object>` audio asset to the browser `<audio>` player."""
  if not uri or not uri.strip():
    raise HTTPException(status_code=400, detail="Query parameter `uri` is required")
  try:
    local_path = await asyncio.to_thread(
        GCS_MANAGER.download_arbitrary_gs_uri, uri.strip()
    )
    return FileResponse(local_path, media_type=_guess_audio_mime(local_path))
  except Exception as e:
    raise HTTPException(
        status_code=404, detail=f"Failed to stream GCS object ({uri}): {e}"
    ) from e


@app.get("/api/gcs/discover")
async def api_gcs_discover_get(
    bucket: str = "",
    prefix: str = "",
    scan_entire_bucket: bool = True,
    auto_register: bool = True,
    auto_load_dataset: bool = True,
    force_reload_dataset: bool = False,
):
  raw_p = prefix if prefix != "" else ("" if scan_entire_bucket else None)
  return await asyncio.to_thread(
      GCS_MANAGER.discover_assets,
      bucket_override=bucket,
      raw_prefix=raw_p,
      scan_entire_bucket_if_empty=scan_entire_bucket,
      auto_register=auto_register,
      auto_load_dataset=auto_load_dataset,
      force_reload_dataset=force_reload_dataset,
  )


@app.post("/api/gcs/discover")
async def api_gcs_discover_post(payload: dict = Body(default={})):
  """Discovers audio & dataset assets in GCS and optionally updates the default GCS config."""
  if payload.get("save_config") and payload.get("gcs_bucket_name") is not None:
    CONFIG_STORE.update({
        "gcs_bucket_name": payload.get("gcs_bucket_name"),
        "gcs_object_prefix": payload.get("gcs_object_prefix", "music-studio"),
        "gcs_auto_sync": payload.get("gcs_auto_sync", True),
    })
  bucket = (
      payload.get("bucket")
      or payload.get("gcs_bucket_name")
      or ""
  ).strip()
  raw_prefix = payload.get("raw_prefix")
  scan_all = bool(payload.get("scan_entire_bucket", True))
  auto_reg = bool(payload.get("auto_register", True))
  auto_load_ds = bool(payload.get("auto_load_dataset", True))
  force_reload_ds = bool(payload.get("force_reload_dataset", False))
  return await asyncio.to_thread(
      GCS_MANAGER.discover_assets,
      bucket_override=bucket,
      raw_prefix=raw_prefix,
      scan_entire_bucket_if_empty=scan_all,
      auto_register=auto_reg,
      auto_load_dataset=auto_load_ds,
      force_reload_dataset=force_reload_ds,
  )


@app.post("/api/gcs/load-dataset")
async def api_gcs_load_dataset(payload: dict = Body(...)):
  """Directly loads a Prompt List / Dataset (`.xlsx`, `.csv`, `.tsv`, `.json`, `.txt`) from a `gs://` URI in GCS.

  Supports:
  - `mode = "append"` (default): adds items to uploaded prompt lists (`U组`) and persists to `UPLOADS_LISTS_DIR`
  - `mode = "replace_builtin"`: replaces the active `BUILTIN_DATASET["items"]` so the entire workbench runs on GCS data
  - Automatic resolution of relative audio paths or `gs://` audio URLs in the dataset to `/api/gcs/stream?uri=...`
  """
  gs_uri = (payload.get("gs_uri") or payload.get("uri") or "").strip()
  if not gs_uri:
    raise HTTPException(status_code=400, detail="gs_uri is required")
  mode = (payload.get("mode") or "append").strip().lower()

  bucket, obj_name = GCSStorageManager.parse_gs_uri(
      gs_uri, default_bucket=GCS_MANAGER.bucket_name
  )
  if not bucket or not obj_name:
    raise HTTPException(status_code=400, detail=f"Invalid GCS URI: {gs_uri}")

  try:
    raw_bytes = await asyncio.to_thread(
        GCS_MANAGER.read_arbitrary_gs_bytes, f"gs://{bucket}/{obj_name}"
    )
  except Exception as e:
    raise HTTPException(
        status_code=404, detail=f"Failed to download dataset from GCS ({gs_uri}): {e}"
    ) from e

  fname = Path(obj_name).name
  parent_dir = str(Path(obj_name).parent).strip(".")
  try:
    items = parse_uploaded_prompt_list(fname, raw_bytes)
  except Exception as e:
    raise HTTPException(
        status_code=400, detail=f"Failed to parse GCS dataset {fname}: {e}"
    ) from e

  # Resolve any relative audio filenames in `result_audio_url` against the same GCS bucket/folder
  for it in items:
    ref_u = (it.get("result_audio_url") or "").strip()
    if ref_u and not ref_u.startswith(("http://", "https://", "/api/", "gs://")):
      rel_obj = f"{parent_dir}/{ref_u}".lstrip("/") if parent_dir else ref_u.lstrip("/")
      full_gs = f"gs://{bucket}/{rel_obj}"
      it["gcs_reference_uri"] = full_gs
      it["result_audio_url"] = (
          f"/api/gcs/stream?uri={urllib.parse.quote(full_gs, safe='')}"
      )

  list_id = f"plist_gcs_{uuid.uuid4().hex[:8]}"
  record = {
      "list_id": list_id,
      "original_filename": f"[GCS] gs://{bucket}/{obj_name}",
      "gs_uri": f"gs://{bucket}/{obj_name}",
      "uploaded_at": datetime.now(timezone.utc).isoformat(),
      "mode": mode,
      "count": len(items),
      "items": items,
  }
  parsed_path = UPLOADS_LISTS_DIR / f"{list_id}.json"
  parsed_path.write_text(
      json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
  )

  if mode == "replace_builtin":
    _apply_items_to_builtin_dataset(
        items,
        source_label=f"gs://{bucket}/{obj_name}",
        audio_assets=None,
    )

  return record


@app.post("/api/gcs/bind-reference-audio")
async def api_gcs_bind_reference_audio(payload: dict = Body(default={})):
  """Dynamically binds GCS audio assets as the reference audio (`result_audio_url`) for dataset items,

  replacing hardcoded external URLs with live `/api/gcs/stream?uri=gs://...` streams.
  Supports:
  1. Single item binding: `{"item_id": "A01", "gs_uri": "gs://bucket/path/track.mp3"}`
  2. Auto-match across bucket: `{"auto_match": True, "bucket": "...", "raw_prefix": "..."}`
  """
  item_id = (payload.get("item_id") or "").strip()
  gs_uri = (payload.get("gs_uri") or "").strip()
  bound_items = []

  if item_id and gs_uri:
    b_name, obj_path = GCSStorageManager.parse_gs_uri(
        gs_uri, default_bucket=GCS_MANAGER.bucket_name
    )
    if not b_name or not obj_path:
      raise HTTPException(status_code=400, detail=f"Invalid gs_uri: {gs_uri}")
    canonical_gs = f"gs://{b_name}/{obj_path}"
    stream_url = f"/api/gcs/stream?uri={urllib.parse.quote(canonical_gs, safe='')}"
    target_upper = item_id.upper()
    for it in BUILTIN_DATASET.get("items", []):
      cand_ids = {
          str(it.get("id") or "").upper(),
          str(it.get("seq") or "").upper(),
          str(it.get("taskid") or "").upper(),
      }
      if target_upper in cand_ids:
        it["result_audio_url"] = stream_url
        it["gcs_reference_uri"] = canonical_gs
        bound_items.append({
            "item_id": it.get("id"),
            "seq": it.get("seq"),
            "gs_uri": canonical_gs,
            "stream_url": stream_url,
        })
    return {"ok": True, "bound_count": len(bound_items), "bound_items": bound_items}

  # Auto-match mode: discover all audio files in GCS and match by `item_id_guess`, `seq`, or `taskid`
  disc = await asyncio.to_thread(
      GCS_MANAGER.discover_assets,
      bucket_override=(payload.get("bucket") or "").strip(),
      raw_prefix=payload.get("raw_prefix"),
      scan_entire_bucket_if_empty=True,
      auto_register=True,
  )
  audio_assets = disc.get("audio_assets", [])
  by_guess = {}
  for a in audio_assets:
    guess = (a.get("item_id_guess") or "").upper()
    if guess and guess not in by_guess:
      by_guess[guess] = a

  for it in BUILTIN_DATASET.get("items", []):
    cand_keys = [
        str(it.get("id") or "").upper(),
        str(it.get("seq") or "").upper(),
        str(it.get("taskid") or "").upper(),
    ]
    matched = None
    for k in cand_keys:
      if k and k in by_guess:
        matched = by_guess[k]
        break
    if matched:
      it["result_audio_url"] = matched["stream_url"]
      it["gcs_reference_uri"] = matched["gs_uri"]
      bound_items.append({
          "item_id": it.get("id"),
          "seq": it.get("seq"),
          "gs_uri": matched["gs_uri"],
          "stream_url": matched["stream_url"],
      })

  return {
      "ok": True,
      "scanned_audio_count": len(audio_assets),
      "bound_count": len(bound_items),
      "bound_items": bound_items,
  }


@app.post("/api/gcs/test")
async def api_gcs_test(payload: dict = Body(default={})):
  if payload.get("gcs_bucket_name") is not None:
    CONFIG_STORE.update({
        "gcs_bucket_name": payload.get("gcs_bucket_name"),
        "gcs_object_prefix": payload.get("gcs_object_prefix", "music-studio"),
        "gcs_auto_sync": payload.get("gcs_auto_sync", True),
    })
  ping_res = GCS_MANAGER.upload_json(
      {
          "service": "music-studio",
          "ping_at": datetime.now(timezone.utc).isoformat(),
      },
      "health/gcs_ping.json",
      force=True,
  )
  disc_res = await asyncio.to_thread(
      GCS_MANAGER.discover_assets,
      scan_entire_bucket_if_empty=True,
      auto_register=True,
  )
  return {"upload_test": ping_res, "list_test": disc_res, "discover": disc_res}


@app.get("/api/cost/dashboard")
async def api_get_cost_dashboard():
  """Returns full-chain per-song & per-group (A / B / C / U) cost analytics across Gemini 4 compilation, Lyria 3 generation, and Gemini 4 deep audio analysis."""
  return await asyncio.to_thread(CostAnalyticsEngine.build_cost_dashboard_report)


@app.post("/api/cost/pricing")
async def api_update_cost_pricing(payload: dict = Body(...)):
  """Updates unit pricing configuration (`pricing` block in `CONFIG_STORE`) and returns the recalculated cost dashboard."""
  pricing_patch = payload.get("pricing") if isinstance(payload.get("pricing"), dict) else payload
  CONFIG_STORE.update({"pricing": pricing_patch})
  return await asyncio.to_thread(CostAnalyticsEngine.build_cost_dashboard_report)


@app.get("/api/cost/export.csv")
async def api_export_cost_csv(group: str = "ALL", scope: str = "all_dataset"):
  """Downloads per-song full-chain cost breakdown CSV (with date-time columns) and automatically archives a timestamped copy to `workspace/cost_reports/` & GCS."""
  snap = await asyncio.to_thread(
      CostAnalyticsEngine.save_and_archive_cost_snapshot,
      group=group,
      scope=scope,
      trigger="csv_download",
  )
  fname = snap["csv_filename"]
  csv_path = COST_REPORTS_DIR / fname
  csv_bytes = csv_path.read_bytes() if csv_path.exists() else b""
  return Response(
      content=csv_bytes,
      media_type="text/csv; charset=utf-8",
      headers={"Content-Disposition": f'attachment; filename="{fname}"'},
  )


@app.post("/api/cost/archive")
async def api_archive_cost_snapshot(payload: dict = Body(default={})):
  """Saves and archives a timestamped CSV + JSON cost ledger snapshot to `workspace/cost_reports/` and `gs://<bucket>/<prefix>/cost_reports/`."""
  group = (payload or {}).get("group") or "ALL"
  scope = (payload or {}).get("scope") or "all_dataset"
  snap = await asyncio.to_thread(
      CostAnalyticsEngine.save_and_archive_cost_snapshot,
      group=group,
      scope=scope,
      trigger="manual_archive",
  )
  archives = await asyncio.to_thread(CostAnalyticsEngine.list_archived_cost_reports, 25)
  return {"ok": True, "snapshot": snap, "archived_reports": archives}


@app.get("/api/cost/archives")
async def api_list_cost_archives(limit: int = 25):
  """Returns the list of timestamped cost report snapshots stored in `workspace/cost_reports/` and GCS."""
  archives = await asyncio.to_thread(CostAnalyticsEngine.list_archived_cost_reports, limit)
  return {"ok": True, "archived_reports": archives}


@app.get("/api/cost/archives/download/{filename}")
async def api_download_cost_archive(filename: str):
  """Downloads a previously saved timestamped CSV or JSON cost snapshot."""
  safe_name = Path(filename).name
  fpath = COST_REPORTS_DIR / safe_name
  if not fpath.exists():
    raise HTTPException(status_code=404, detail=f"Cost snapshot file not found: {safe_name}")
  mtype = "application/json; charset=utf-8" if safe_name.endswith(".json") else "text/csv; charset=utf-8"
  return FileResponse(path=str(fpath), media_type=mtype, filename=safe_name)


if __name__ == "__main__":
  import uvicorn

  port = int(os.environ.get("PORT", "8080"))
  uvicorn.run("music_studio_app:app", host="0.0.0.0", port=port, reload=False)
