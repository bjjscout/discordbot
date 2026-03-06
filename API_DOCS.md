# WhisperX API Documentation

## Base URL
```
https://whisperx.jeffrey-epstein.com
```

## Authentication
No authentication required. The API is publicly accessible.

---

## Architecture Overview

This API uses **12 uvicorn workers** with **Redis-backed shared storage** for job state management. 

### Concurrency Control

**Transcription Jobs:**
- Uses GPU (CUDA) for inference
- **Max concurrent:** 4 transcription jobs (prevents CUDA OOM)
- Videos ≤30 min: Uses `base` model, 4 slots
- Videos >30 min: Uses `medium` model, 1 slot
- Jobs automatically queue when GPU is busy

**FFmpeg Jobs:**
- CPU-bound processing (no GPU)
- **Max concurrent:** 8 FFmpeg jobs (prevents CPU overload)
- Runs independently of transcription jobs

### Key Features:
- **Async job processing** with polling-based status checks
- **Shared Redis storage** - Job state accessible across all 12 workers
- **Distributed locking** - Prevents resource conflicts
- **R2 Cloudflare integration** - Results uploaded with presigned URLs (24h expiry)
- **FFmpeg video processing** - Trim, burn subtitles, extract audio, crop, resize

---

## API Endpoints Overview

| Method | Endpoint | Description | Returns |
|--------|----------|-------------|---------|
| GET | `/health` | Health check | Status |
| GET | `/` | API info | API version |
| POST | `/transcribe/url` | Submit URL (async) | Job ID |
| POST | `/transcribe/url/sync` | Submit URL (sync) | Result |
| POST | `/transcribe` | Upload audio (async) | Job ID |
| POST | `/transcribe/sync` | Upload audio (sync) | Result |
| GET | `/jobs/{job_id}` | Poll job status | Job result |
| DELETE | `/jobs/{job_id}` | Delete specific job | Confirmation |
| DELETE | `/jobs/all` | Delete all completed/failed jobs | Deleted count |
| POST | `/jobs/cleanup` | Manually trigger cleanup | Cleanup stats |
| GET | `/jobs` | List all jobs (debug) | Job list |
| POST | `/ffmpeg` | Process video (async) | Job ID |
| POST | `/ffmpeg/sync` | Process video (sync) | Output URL |
| POST | `/download` | Download video from URL | Job ID |
| GET | `/models` | List Whisper models | Model list |
| GET | `/languages` | List supported languages | Language list |

---

## 1. Health Check

**Endpoint:** `GET /health`

**Example:**
```bash
curl https://whisperx.jeffrey-epstein.com/health
```

**Response:**
```json
{
  "status": "healthy",
  "r2": "configured",
  "r2_bucket": "your-bucket-name"
}
```

---

## 2. Submit Transcription from URL (Async) - RECOMMENDED

Use this for YouTube videos, external audio files, or any media URL. Returns a job ID immediately for polling.

**Endpoint:** `POST /transcribe/url`

**Example:**
```bash
curl -X POST https://whisperx.jeffrey-epstein.com/transcribe/url \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "model=large-v3" \
  -F "task=translate"
```

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| url | string | Yes | - | URL to audio/video (YouTube, direct MP3, etc.) |
| model | string | No | large-v3 | Whisper model: tiny, base, small, medium, large-v2, large-v3 |
| language | string | No | auto | Language code (e.g., en, zh, ja) or leave empty for auto-detect |
| task | string | No | translate | transcribe (keep original language) or translate (to English) |
| diarize | bool | No | false | Enable speaker diarization |
| hf_token | string | No | - | HuggingFace token (required for diarization) |
| min_speakers | int | No | - | Minimum number of speakers (for diarization) |
| max_speakers | int | No | - | Maximum number of speakers (for diarization) |
| batch_size | int | No | 8 | Batch size for inference |
| compute_type | string | No | float16 | float16, float32, int8 |
| device | string | No | cuda | cuda or cpu |
| vad_method | string | No | pyannote | pyannote or silero |
| vad_onset | float | No | 0.500 | VAD onset threshold |
| vad_offset | float | No | 0.363 | VAD offset threshold |

**Response:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "downloading",
  "result": null,
  "error": null
}
```

---

## 3. Submit Transcription from URL (Sync)

Same as above but waits for completion. Use for short audio (< 5 minutes).

**Endpoint:** `POST /transcribe/url/sync`

**Example:**
```bash
curl -X POST https://whisperx.jeffrey-epstein.com/transcribe/url/sync \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "task=translate"
```

**Response (on success):**
```json
{
  "urls": {
    "txt": "https://...r2.cloudflarestorage.com/xxx.txt?...",
    "srt": "https://...r2.cloudflarestorage.com/xxx.srt?...",
    "ass": "https://...r2.cloudflarestorage.com/xxx.ass?..."
  },
  "preview": "First 50 words of transcription..."
}
```

---

## 4. Upload Audio File (Async)

Upload audio file directly. Returns job ID for polling.

**Endpoint:** `POST /transcribe`

**Example:**
```bash
curl -X POST https://whisperx.jeffrey-epstein.com/transcribe \
  -F "file=@audio.mp3" \
  -F "model=large-v3" \
  -F "task=translate"
```

**Parameters:** Same as `/transcribe/url` (replace `url` with `file`)

**Response:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued",
  "result": null,
  "error": null
}
```

---

## 5. Upload Audio File (Sync)

Upload and wait for result. Use for short audio (< 5 minutes).

**Endpoint:** `POST /transcribe/sync`

**Example:**
```bash
curl -X POST https://whisperx.jeffrey-epstein.com/transcribe/sync \
  -F "file=@audio.mp3" \
  -F "task=translate"
```

---

## 6. Poll Job Status

Poll this endpoint with the job_id to get the transcription result.

**Endpoint:** `GET /jobs/{job_id}`

**Example:**
```bash
curl https://whisperx.jeffrey-epstein.com/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response - Processing:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "processing",
  "result": null,
  "error": null
}
```

**Response - Waiting for GPU (Queue):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued",
  "result": null,
  "error": null
}
```
*Note: Jobs automatically queue when another transcription is running. Poll every 5 seconds.*

**Response - Completed (Success):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "result": {
    "urls": {
      "txt": "https://...r2.cloudflarestorage.com/xxx.txt?...",
      "srt": "https://...r2.cloudflarestorage.com/xxx.srt?...",
      "ass": "https://...r2.cloudflarestorage.com/xxx.ass?..."
    },
    "preview": "First 50 words of transcription..."
  },
  "error": null
}
```

**Response - Failed:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "failed",
  "result": null,
  "error": "Error message here"
}
```

---

## 7. Delete Job

Delete a completed job to free memory.

**Endpoint:** `DELETE /jobs/{job_id}`

**Example:**
```bash
curl -X DELETE https://whisperx.jeffrey-epstein.com/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Response:**
```json
{
  "message": "Job deleted"
}
```

---

## 8. Delete All Non-Running Jobs (Emergency Cleanup)

Delete all completed and failed jobs at once.

**Endpoint:** `DELETE /jobs/all`

**Example:**
```bash
curl -X DELETE https://whisperx.jeffrey-epstein.com/jobs/all
```

**Response:**
```json
{
  "message": "Deleted all non-running jobs",
  "deleted": 5
}
```

---

## 9. FFmpeg Video Processing

Process video files with FFmpeg. Supports predefined operations and custom arguments.

### Two Modes

| Mode | Endpoint | Description | Use Case |
|------|----------|-------------|----------|
| **Async (Recommended)** | `/ffmpeg` | Returns job_id immediately, poll for status | Long videos, multiple jobs |
| **Sync** | `/ffmpeg/sync` | Blocks until complete | Short videos, quick results |

**Concurrency:** Up to 8 concurrent FFmpeg jobs (CPU-bound)

### Supported Operations:**

| Operation | Description |
|-----------|-------------|
| trim | Cut video from start to end time |
| burn_subtitles | Hardcode SRT/ASS subtitles onto video |
| extract_audio | Extract audio track to file |
| crop | Crop video to specified dimensions |
| resize | Resize video to new dimensions |
| speed | Adjust playback speed |
| custom | Run custom FFmpeg command |

### 9.1 Trim Video

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=trim" \
  -F "start=10" \
  -F "end=60"
```

**Parameters:**
- `start` (float): Start time in seconds
- `end` (float): End time in seconds (use either end OR duration, not both)
- `duration` (float): Duration in seconds (alternative to end)

### 9.2 Burn Subtitles

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=burn_subtitles" \
  -F "subtitle_url=https://example.com/subs.srt"
```

**Parameters:**
- `subtitle_url` (string, required): URL to SRT or ASS subtitle file

### 9.3 Extract Audio

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=extract_audio" \
  -F "audio_codec=mp3" \
  -F "audio_bitrate=192k"
```

**Parameters:**
- `audio_codec` (string): mp3, aac, flac, wav, ogg (default: mp3)
- `audio_bitrate` (string): 128k, 192k, 256k, 320k (default: 192k)

### 9.4 Crop Video

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=crop" \
  -F "crop_width=1920" \
  -F "crop_height=1080" \
  -F "crop_x=0" \
  -F "crop_y=0"
```

**Parameters:**
- `crop_width` (int): Width of crop area
- `crop_height` (int): Height of crop area
- `crop_x` (int): X position (default: 0)
- `crop_y` (int): Y position (default: 0)

### 9.5 Resize Video

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=resize" \
  -F "resize_width=1920" \
  -F "resize_height=1080"
```

**Parameters:**
- `resize_width` (int): New width
- `resize_height` (int): New height

### 9.6 Change Playback Speed

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=speed" \
  -F "speed=1.5"
```

**Parameters:**
- `speed` (float): Speed multiplier (0.5 = half speed, 1.5 = 1.5x speed, 2.0 = double)

### 9.7 Custom FFmpeg Command

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=custom" \
  -F "custom_args=-vf hflip -c:v libx264 -preset fast -crf 23"
```

**Parameters:**
- `custom_args` (string): Raw FFmpeg arguments

### 9.8 Reformat Video

Convert video to landscape, reel (vertical), or square format.

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=reformat" \
  -F "format=reel" \
  -F "fill_mode=crop"
```

**Parameters:**
- `format` (string): Target format - "landscape", "reel", or "square"
- `fill_mode` (string): How to fill - "crop" (default), "pad" (black bars), "blur_background"

### 9.9 Add Overlay

Add logo/image overlay to video.

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=overlay" \
  -F "overlay_url=https://example.com/logo.png" \
  -F "position=top-center" \
  -F "opacity=0.8"
```

**Parameters:**
- `overlay_url` (string, required): URL to overlay image (PNG recommended)
- `position` (string): Position - "top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right", "center" (default: "top-center")
- `opacity` (float): Opacity 0.0-1.0 (default: 0.8)
- `scale` (float): Scale relative to video width (default: 0.2)
- `margin` (int): Pixels from edge (default: 20)

### 9.10 Loop Video

Loop video to target duration or count.

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg" \
  -F "url=https://example.com/short.mp4" \
  -F "operation=loop" \
  -F "target_duration=30"
```

**Parameters:**
- `target_duration` (float): Target duration in seconds (alternative to loop_count)
- `loop_count` (int): Number of times to loop

### FFmpeg Response (Async - /ffmpeg)

```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued"
}
```

**Poll for status:**
```bash
curl https://whisperx.jeffrey-epstein.com/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Completed response:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "result": {
    "operation": "trim",
    "output_url": "https://...r2.cloudflarestorage.com/ffmpeg_trim_abc123.mp4?expires=...",
    "expires_in": 86400
  }
}
```

---

## 10. FFmpeg Sync (Backward Compatible)

Process video synchronously - blocks until complete. Use for short videos.

**Endpoint:** `POST /ffmpeg/sync`

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/ffmpeg/sync" \
  -F "url=https://example.com/video.mp4" \
  -F "operation=trim" \
  -F "start=10" \
  -F "end=60"
```

**Response:**
```json
{
  "operation": "trim",
  "output_url": "https://...r2.cloudflarestorage.com/ffmpeg_trim_abc123.mp4?expires=...",
  "expires_in": 86400
}
```

---

## 11. Manual Cleanup Trigger

Manually trigger the cleanup process.

**Endpoint:** `POST /jobs/cleanup`

**Example:**
```bash
curl -X POST https://whisperx.jeffrey-epstein.com/jobs/cleanup
```

**Response:**
```json
{
  "message": "Cleanup completed",
  "deleted": 3,
  "failed": 1
}
```

---

## 12. List All Jobs (Debug)

List all current jobs with their status and age.

**Endpoint:** `GET /jobs`

**Example:**
```bash
curl https://whisperx.jeffrey-epstein.com/jobs
```

**Response:**
```json
{
  "jobs": [
    {
      "job_id": "abc123",
      "status": "completed",
      "created_at": "2024-01-01T12:00:00",
      "has_result": true,
      "has_error": false
    }
  ],
  "total": 1
}
```

---

## 13. List Available Models

**Endpoint:** `GET /models`

**Example:**
```bash
curl https://whisperx.jeffrey-epstein.com/models
```

**Response:**
```json
{
  "models": [
    {"name": "tiny", "description": "Smallest model, fastest, lowest accuracy"},
    {"name": "base", "description": "Small model, good balance"},
    {"name": "small", "description": "Medium model, better accuracy"},
    {"name": "medium", "description": "Large model, high accuracy"},
    {"name": "large-v2", "description": "Large model v2, high accuracy"},
    {"name": "large-v3", "description": "Latest large model, best accuracy"}
  ]
}
```

---

## 15. Download Video

Download video from URL using yt-dlp. Supports YouTube, Rumble, Twitter, Vimeo, and 1700+ other sites.

**Endpoint:** `POST /download`

**Example:**
```bash
curl -X POST "https://whisperx.jeffrey-epstein.com/download" \
  -F "url=https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  -F "quality=best"
```

**Parameters:**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| url | string | Yes | - | URL to video (YouTube, Rumble, Twitter, etc.) |
| quality | string | No | best | Quality: best, 720p, 480p, worst |
| extract_audio | bool | No | false | Also extract audio as MP3 |

**Response (async - returns job_id):**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "queued"
}
```

**Poll for status:**
```bash
curl https://whisperx.jeffrey-epstein.com/jobs/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**Completed response:**
```json
{
  "job_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "completed",
  "result": {
    "video_url": "https://...r2.cloudflarestorage.com/download_xxx.mp4?expires=...",
    "audio_url": "https://...r2.cloudflarestorage.com/download_xxx.mp3?expires=...",
    "duration": 120.5,
    "width": 1920,
    "height": 1080,
    "format": "landscape",
    "title": "Video Title"
  }
}
```

---

## 16. List Supported Languages

**Endpoint:** `GET /languages`

**Example:**
```bash
curl https://whisperx.jeffrey-epstein.com/languages
```

---

## Job Status Values

| Status | Description | Action |
|--------|-------------|--------|
| queued | Job submitted, waiting for GPU slot | Poll every 5 seconds |
| downloading | Downloading audio from URL | Poll every 5 seconds |
| processing | Transcribing with WhisperX | Poll every 5 seconds |
| completed | Done! Check result.urls | Download from URLs |
| failed | Error occurred | Check error field |

---

## Supported URL Types

- **YouTube:** `https://www.youtube.com/watch?v=...`, `https://youtu.be/...`, `https://www.youtube.com/shorts/...`
- **Direct Audio:** `.mp3`, `.wav`, `.ogg`, `.flac`, `.webm`, `.m4a`, `.aac`
- **Other Sites:** Vimeo, Twitter/X, SoundCloud, Twitch, Facebook, Instagram, and 1700+ sites via yt-dlp

---

## Output Formats

| Format | Description | File Extension |
|--------|-------------|-----------------|
| txt | Plain text transcription | .txt |
| srt | SubRip subtitles (segment timing) | .srt |
| ass | Advanced SubStation Alpha (word-level timing) | .ass |

---

## Architecture Notes for AI Agents

### Concurrency Control

The API uses a **transcription lock** to prevent CUDA OOM errors:
- Only **1 transcription job runs at a time**
- Additional jobs automatically **queue** with status `"queued"`
- Queue is managed via Redis across all 12 workers
- Poll every 5 seconds to check for slot availability

### Worker Distribution

With 12 workers:
- Worker handling transcription: 1 (locked)
- Workers available for API/polling: 11
- This allows responsive status checks even during heavy processing

### Redis Shared Storage

Job state is stored in Redis, not in-memory:
- Job creation, status updates, and results are instantly visible to all workers
- Survives worker restarts
- Enables true horizontal scaling

### Typical Workflow

1. Submit job → Get `job_id` immediately
2. Job status cycles: `queued` → `downloading` → `processing` → `completed`/`failed`
3. If status is `queued`, another job is currently transcribing - keep polling
4. On `completed`, result contains R2 URLs (valid for 24 hours)
5. Download files from URLs before expiry

---

## Complete Agent Workflow Example

### Python - Submit URL and Poll for Result

```python
import requests
import time

BASE_URL = "https://whisperx.jeffrey-epstein.com"

def transcribe_url(url: str, model: str = "large-v3", task: str = "translate") -> dict:
    """
    Submit a URL for transcription and return the result.
    
    Args:
        url: URL to audio/video
        model: Whisper model (tiny, base, small, medium, large-v2, large-v3)
        task: "transcribe" or "translate"
    
    Returns:
        dict with keys: txt_url, srt_url, ass_url, preview
    """
    # Step 1: Submit job
    response = requests.post(
        f"{BASE_URL}/transcribe/url",
        data={
            "url": url,
            "model": model,
            "task": task
        }
    )
    response.raise_for_status()
    job_data = response.json()
    job_id = job_data["job_id"]
    
    print(f"Job submitted: {job_id}")
    
    # Step 2: Poll for result
    while True:
        status_response = requests.get(f"{BASE_URL}/jobs/{job_id}")
        status_data = status_response.json()
        
        status = status_data["status"]
        
        if status == "completed":
            result = status_data["result"]
            
            # Check if there was an error during processing
            if result is None or "error" in result and "urls" not in result:
                raise Exception(f"Transcription failed: {result.get('error', 'Unknown error')}")
            
            # Success - return URLs
            return {
                "txt_url": result["urls"]["txt"],
                "srt_url": result["urls"]["srt"],
                "ass_url": result["urls"]["ass"],
                "preview": result["preview"]
            }
        
        elif status == "failed":
            raise Exception(f"Transcription failed: {status_data['error']}")
        
        elif status in ["queued", "downloading", "processing"]:
            print(f"Status: {status}, waiting...")
            time.sleep(5)
        
        else:
            raise Exception(f"Unknown status: {status}")

# Usage
result = transcribe_url("https://www.youtube.com/watch?v=VIDEO_ID")
print(f"TXT: {result['txt_url']}")
print(f"SRT: {result['srt_url']}")
print(f"ASS: {result['ass_url']}")
print(f"Preview: {result['preview']}")
```

### Python - FFmpeg Video Processing

```python
import requests

def trim_video(video_url: str, start: float, end: float) -> str:
    """Trim a video and return the output URL."""
    response = requests.post(
        "https://whisperx.jeffrey-epstein.com/ffmpeg",
        data={
            "url": video_url,
            "operation": "trim",
            "start": start,
            "end": end
        }
    )
    response.raise_for_status()
    return response.json()["output_url"]

def burn_subtitles(video_url: str, subtitle_url: str) -> str:
    """Burn subtitles into a video."""
    response = requests.post(
        "https://whisperx.jeffrey-epstein.com/ffmpeg",
        data={
            "url": video_url,
            "operation": "burn_subtitles",
            "subtitle_url": subtitle_url
        }
    )
    response.raise_for_status()
    return response.json()["output_url"]

def extract_audio(video_url: str, codec: str = "mp3") -> str:
    """Extract audio from a video."""
    response = requests.post(
        "https://whisperx.jeffrey-epstein.com/ffmpeg",
        data={
            "url": video_url,
            "operation": "extract_audio",
            "audio_codec": codec
        }
    )
    response.raise_for_status()
    return response.json()["output_url"]
```

### JavaScript/Node.js - Submit URL and Poll for Result

```javascript
const BASE_URL = "https://whisperx.jeffrey-epstein.com";

async function transcribeUrl(url, model = "large-v3", task = "translate") {
    // Step 1: Submit job
    const formData = new FormData();
    formData.append("url", url);
    formData.append("model", model);
    formData.append("task", task);
    
    const response = await fetch(`${BASE_URL}/transcribe/url`, {
        method: "POST",
        body: formData
    });
    
    const jobData = await response.json();
    const jobId = jobData.job_id;
    console.log(`Job submitted: ${jobId}`);
    
    // Step 2: Poll for result
    while (true) {
        const statusResponse = await fetch(`${BASE_URL}/jobs/${jobId}`);
        const statusData = await statusResponse.json();
        
        const status = statusData.status;
        
        if (status === "completed") {
            const result = statusData.result;
            
            if (!result || (result.error && !result.urls)) {
                throw new Error(`Transcription failed: ${result?.error || "Unknown error"}`);
            }
            
            return {
                txt_url: result.urls.txt,
                srt_url: result.urls.srt,
                ass_url: result.urls.ass,
                preview: result.preview
            };
        } 
        else if (status === "failed") {
            throw new Error(`Transcription failed: ${statusData.error}`);
        } 
        else {
            console.log(`Status: ${status}, waiting...`);
            await new Promise(r => setTimeout(r, 5000));
        }
    }
}

// Usage
const result = await transcribeUrl("https://www.youtube.com/watch?v=VIDEO_ID");
console.log(`TXT: ${result.txt_url}`);
console.log(`SRT: ${result.srt_url}`);
console.log(`ASS: ${result.ass_url}`);
```

### curl - Complete Workflow

```bash
# Step 1: Submit job
JOB_RESPONSE=$(curl -s -X POST "https://whisperx.jeffrey-epstein.com/transcribe/url" \
  -F "url=https://www.youtube.com/watch?v=VIDEO_ID" \
  -F "model=large-v3" \
  -F "task=translate")

JOB_ID=$(echo $JOB_RESPONSE | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
echo "Job ID: $JOB_ID"

# Step 2: Poll for result
while true; do
  STATUS_RESPONSE=$(curl -s "https://whisperx.jeffrey-epstein.com/jobs/$JOB_ID")
  STATUS=$(echo $STATUS_RESPONSE | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
  echo "Status: $STATUS"
  
  if [ "$STATUS" = "completed" ]; then
    echo "Done!"
    echo $STATUS_RESPONSE | grep -o '"urls":{[^}]*}'
    break
  elif [ "$STATUS" = "failed" ]; then
    echo "Failed!"
    echo $STATUS_RESPONSE
    break
  fi
  
  sleep 5
done
```

---

## Error Handling

Common errors and solutions:

| Error | Cause | Solution |
|-------|-------|----------|
| "CUDA out of memory" | Transcription lock failed or GPU overloaded | Retry after current job completes |
| "R2 not configured" | Missing R2 environment variables | Check server configuration |
| "No such container" | Docker issue | Restart Docker |
| "Timeout waiting for slot" | Queue full, too many jobs | Wait and retry |

---

## Rate Limiting & Best Practices

1. **Poll interval:** Wait at least 5 seconds between status checks
2. **Job cleanup:** Delete completed jobs to free memory
3. **URL expiry:** Download results within 24 hours (R2 presigned URLs expire)
4. **Queue awareness:** Status `"queued"` means GPU is busy - this is normal
5. **Error handling:** Always check for `"failed"` status and handle errors gracefully
