import os
import time
import uuid
import requests
from flask import Flask, render_template, request, jsonify
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
UPLOAD_DIR = os.path.join(app.root_path, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Configure these in your hosting provider's environment variables.
KLING_API_KEY = os.getenv("KLING_API_KEY", "").strip()
KLING_API_BASE = os.getenv("KLING_API_BASE", "https://api-singapore.klingai.com").rstrip("/")
TEXT_ENDPOINT = f"{KLING_API_BASE}/text-to-video/kling-3.0"
IMAGE_ENDPOINT = f"{KLING_API_BASE}/image-to-video/kling-3.0"

@app.get("/")
def index():
    return render_template("index.html")

@app.get("/api/config")
def config():
    return jsonify({"configured": bool(KLING_API_KEY)})

@app.post("/api/generate")
def generate():
    if not KLING_API_KEY:
        return jsonify({
            "error": "Kling API key is not configured. Add KLING_API_KEY in your hosting environment variables."
        }), 503

    mode = request.form.get("mode", "text")
    prompt = (request.form.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"error": "Enter a video prompt."}), 400
    if len(prompt) > 2500:
        return jsonify({"error": "Please keep the prompt under 2,500 characters."}), 400

    try:
        duration = int(request.form.get("duration", "5"))
    except ValueError:
        return jsonify({"error": "Invalid duration."}), 400
    if duration not in [3,4,5,6,7,8,9,10,11,12,13,14,15]:
        return jsonify({"error": "Choose a supported duration from 3 to 15 seconds."}), 400

    resolution = request.form.get("resolution", "720p")
    if resolution not in ["720p", "1080p", "4k"]:
        return jsonify({"error": "Invalid resolution."}), 400
    aspect_ratio = request.form.get("aspect_ratio", "16:9")
    if aspect_ratio not in ["16:9", "9:16", "1:1"]:
        return jsonify({"error": "Invalid aspect ratio."}), 400

    headers = {
        "Authorization": f"Bearer {KLING_API_KEY}",
        "Content-Type": "application/json"
    }
    task_ref = str(uuid.uuid4())

    try:
        if mode == "image":
            image = request.files.get("image")
            if not image or not image.filename:
                return jsonify({"error": "Upload a JPG or PNG image for image-to-video."}), 400
            ext = os.path.splitext(secure_filename(image.filename))[1].lower()
            if ext not in [".jpg", ".jpeg", ".png"]:
                return jsonify({"error": "Only JPG and PNG images are supported."}), 400
            # Kling's API accepts an image URL or base64. This sample uses base64 data.
            import base64
            image_bytes = image.read()
            if len(image_bytes) > 10 * 1024 * 1024:
                return jsonify({"error": "Image must be smaller than 10 MB."}), 400
            image_data = base64.b64encode(image_bytes).decode("ascii")
            mime = "image/png" if ext == ".png" else "image/jpeg"
            image_value = f"data:{mime};base64,{image_data}"
            payload = {
                "contents": [
                    {"type": "prompt", "text": prompt},
                    {"type": "first_frame", "url": image_value}
                ],
                "settings": {
                    "resolution": resolution,
                    "duration": duration,
                    "audio": "off",
                    "multi_shot": False
                },
                "options": {"external_task_id": task_ref}
            }
            endpoint = IMAGE_ENDPOINT
        else:
            payload = {
                "prompt": prompt,
                "settings": {
                    "resolution": resolution,
                    "aspect_ratio": aspect_ratio,
                    "duration": duration,
                    "audio": "off",
                    "multi_shot": False
                },
                "options": {"external_task_id": task_ref}
            }
            endpoint = TEXT_ENDPOINT

        response = requests.post(endpoint, headers=headers, json=payload, timeout=60)
        try:
            data = response.json()
        except ValueError:
            data = {"message": response.text[:500]}
        if response.status_code >= 400:
            return jsonify({"error": data.get("message", "Kling API request failed."), "details": data}), response.status_code

        task_data = data.get("data", {})
        task_id = task_data.get("id") or task_data.get("task_id")
        if not task_id:
            return jsonify({"error": "The API response did not contain a task ID.", "details": data}), 502
        return jsonify({"task_id": task_id, "status": task_data.get("status", "submitted"), "task_ref": task_ref})
    except requests.RequestException as exc:
        return jsonify({"error": f"Could not reach Kling API: {str(exc)}"}), 502

@app.get("/api/task/<task_id>")
def task_status(task_id):
    if not KLING_API_KEY:
        return jsonify({"error": "Kling API key is not configured."}), 503
    # The official task-query endpoint accepts task_ids as a query parameter.
    try:
        response = requests.get(
            f"{KLING_API_BASE}/tasks",
            params={"task_ids": task_id},
            headers={"Authorization": f"Bearer {KLING_API_KEY}"},
            timeout=30
        )
        data = response.json()
        if response.status_code >= 400:
            return jsonify({"error": data.get("message", "Task lookup failed."), "details": data}), response.status_code
        results = data.get("data", {}).get("result", [])
        if not results:
            # Some API response versions may use a different shape; return raw data for diagnosis.
            return jsonify({"status": "processing", "details": data})
        item = results[0]
        outputs = item.get("outputs", [])
        video = next((o for o in outputs if o.get("type") == "video"), None)
        return jsonify({
            "status": item.get("status", "processing"),
            "message": item.get("message", ""),
            "video_url": (video or {}).get("url"),
            "watermark_url": (video or {}).get("watermark_url")
        })
    except (requests.RequestException, ValueError) as exc:
        return jsonify({"error": f"Could not check task: {str(exc)}"}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
