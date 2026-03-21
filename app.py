from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    flash,
    session,
    jsonify,
    send_from_directory,
)

import math
import os
import smtplib
import sqlite3
import traceback
import uuid
from pathlib import Path
from email.message import EmailMessage

try:
    from moviepy import VideoFileClip
except ImportError:
    from moviepy.editor import VideoFileClip

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / "uploads"
GENERATED_CLIPS_FOLDER = BASE_DIR / "generated_clips"
DATABASE_PATH = BASE_DIR / "users.db"

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["GENERATED_CLIPS_FOLDER"] = str(GENERATED_CLIPS_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB max upload

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
GENERATED_CLIPS_FOLDER.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}


def get_base_url():
    if BASE_URL:
        return BASE_URL
    return request.url_root.rstrip("/")


def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_user_plan():
    return "free"


def get_allowed_clip_limit(plan):
    return 20 if plan == "premium" else 3


def get_uploads_left_today(plan):
    return 999 if plan == "premium" else 3


def send_email(to_email, subject, body):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        raise ValueError("SMTP settings missing. Set SMTP_USERNAME and SMTP_PASSWORD.")
    if not EMAIL_FROM:
        raise ValueError("EMAIL_FROM is missing.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"LI3 Media <{EMAIL_FROM}>"
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(msg)


def get_serializer():
    return URLSafeTimedSerializer(app.secret_key)


def generate_token(email):
    return get_serializer().dumps(email, salt="email-verify")


def confirm_token(token):
    return get_serializer().loads(token, salt="email-verify", max_age=86400)


def build_public_clip_url(video_folder, filename):
    return url_for(
        "download_clip",
        video_folder=video_folder,
        filename=filename,
        _external=True,
    )


def format_seconds(seconds):
    seconds = int(seconds)
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes:02d}:{remaining_seconds:02d}"


def merge_overlapping_ranges(ranges, min_gap=1.0):
    if not ranges:
        return []

    ranges = sorted(ranges, key=lambda x: x[0])
    merged = [ranges[0]]

    for start, end, score in ranges[1:]:
        last_start, last_end, last_score = merged[-1]

        if start <= last_end + min_gap:
            merged[-1] = (
                last_start,
                max(last_end, end),
                max(last_score, score),
            )
        else:
            merged.append((start, end, score))

    return merged


def pick_top_moments(scored_moments, clip_limit, min_spacing=3.0):
    picked = []

    for moment in sorted(scored_moments, key=lambda x: x["score"], reverse=True):
        too_close = False

        for chosen in picked:
            if abs(moment["center"] - chosen["center"]) < min_spacing:
                too_close = True
                break

        if not too_close:
            picked.append(moment)

        if len(picked) >= clip_limit:
            break

    return sorted(picked, key=lambda x: x["start"])


def detect_audio_highlights(video, clip_limit):
    if video.audio is None:
        return []

    duration = float(video.duration or 0)
    if duration <= 0:
        return []

    window_size = 2.0
    step_size = 1.0
    clip_length = 20.0

    scored_moments = []
    t = 0.0

    while t < duration:
        window_end = min(t + window_size, duration)

        if window_end <= t:
            break

        try:
            audio_window = video.audio.subclipped(t, window_end)
            sound = audio_window.to_soundarray(fps=11025)

            if len(sound) == 0:
                energy = 0.0
            else:
                # Works for mono or stereo
                if getattr(sound, "ndim", 1) > 1:
                    samples = sound.mean(axis=1)
                else:
                    samples = sound

                energy = float((samples ** 2).mean() ** 0.5)

            audio_window.close()

        except Exception:
            energy = 0.0

        center = t + ((window_end - t) / 2.0)
        start = max(0.0, center - clip_length / 2.0)
        end = min(duration, center + clip_length / 2.0)

        scored_moments.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "center": round(center, 2),
                "score": energy,
            }
        )

        t += step_size

    return pick_top_moments(scored_moments, clip_limit)


def detect_fallback_highlights(duration, clip_limit):
    segment_length = duration / clip_limit
    fallback = []

    for i in range(clip_limit):
        start_time = round(i * segment_length, 2)
        end_time = round(min((i + 1) * segment_length, duration), 2)

        if end_time > start_time:
            fallback.append(
                {
                    "start": start_time,
                    "end": end_time,
                    "center": round((start_time + end_time) / 2.0, 2),
                    "score": 0.0,
                }
            )

    return fallback


def create_real_clips(input_path, clip_limit):
    clips_data = []
    video_folder = uuid.uuid4().hex
    output_folder = os.path.join(app.config["GENERATED_CLIPS_FOLDER"], video_folder)
    os.makedirs(output_folder, exist_ok=True)

    video = None

    try:
        video = VideoFileClip(input_path)
        duration = float(video.duration or 0)

        if duration <= 0:
            raise ValueError("Uploaded video has no usable duration.")

        highlights = detect_audio_highlights(video, clip_limit)

        if not highlights:
            highlights = detect_fallback_highlights(duration, clip_limit)

        for i, moment in enumerate(highlights, start=1):
            start_time = moment["start"]
            end_time = moment["end"]

            if end_time <= start_time:
                continue

            output_filename = f"clip_{i}.mp4"
            output_path = os.path.join(output_folder, output_filename)

            subclip = video.subclipped(start_time, end_time)

            try:
                write_kwargs = {
                    "codec": "libx264",
                    "audio": True,
                    "logger": None,
                }

                if getattr(video, "fps", None):
                    write_kwargs["fps"] = video.fps

                subclip.write_videofile(output_path, **write_kwargs)

            finally:
                subclip.close()

            clips_data.append(
                {
                    "clip_number": i,
                    "start_time": round(start_time, 2),
                    "end_time": round(end_time, 2),
                    "start_label": format_seconds(start_time),
                    "end_label": format_seconds(end_time),
                    "score": round(moment["score"], 6),
                    "download_url": build_public_clip_url(video_folder, output_filename),
                }
            )

        if not clips_data:
            raise ValueError("No clips were created from the uploaded video.")

        return clips_data

    finally:
        if video is not None:
            video.close()

@app.route("/health")
def health():
    return {"status": "ok"}, 200


@app.route("/")
def home():
    if "user" in session:
        plan = get_user_plan()
        return render_template("index.html", username=session["user"], plan=plan)
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password_raw = request.form.get("password", "").strip()

        if not username or not email or not password_raw:
            flash("Username, email, and password are required.")
            return render_template("register.html")

        hashed_password = generate_password_hash(password_raw)

        with get_db() as conn:
            existing_user = conn.execute(
                "SELECT id FROM users WHERE username = ? OR email = ?",
                (username, email),
            ).fetchone()

            if existing_user:
                flash("Username or email already exists.")
                return render_template("register.html")

            conn.execute(
                "INSERT INTO users (username, email, password, verified) VALUES (?, ?, ?, ?)",
                (username, email, hashed_password, 0),
            )
            conn.commit()

        token = generate_token(email)
        verify_link = f"{get_base_url()}/verify/{token}"

        try:
            send_email(
                email,
                "Verify your LI3 Media account",
                f"""Hi {username},

Thanks for signing up.

Click the link below to verify your email:

{verify_link}

This link expires in 24 hours.
""",
            )
            flash("Account created. Check your email to verify your account.")
        except Exception:
            traceback.print_exc()
            flash("Account created, but the verification email could not be sent.")

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/verify/<token>")
def verify(token):
    try:
        email = confirm_token(token)
    except SignatureExpired:
        return "This verification link expired.", 400
    except BadSignature:
        return "Invalid verification link.", 400

    with get_db() as conn:
        user = conn.execute(
            "SELECT id, verified FROM users WHERE email = ?",
            (email,),
        ).fetchone()

        if not user:
            return "Account not found.", 404

        if user["verified"] == 1:
            return "Email already verified. You can log in.", 200

        conn.execute("UPDATE users SET verified = 1 WHERE email = ?", (email,))
        conn.commit()

    return "Email verified! You can now log in.", 200


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Email is required.")
            return render_template("resend_verification.html")

        with get_db() as conn:
            user = conn.execute(
                "SELECT username, email, verified FROM users WHERE email = ?",
                (email,),
            ).fetchone()

        if not user:
            flash("No account found with that email.")
            return render_template("resend_verification.html")

        if user["verified"] == 1:
            flash("That email is already verified. Please log in.")
            return redirect(url_for("login"))

        token = generate_token(user["email"])
        verify_link = f"{get_base_url()}/verify/{token}"

        try:
            send_email(
                user["email"],
                "Verify your LI3 Media account",
                f"""Hi {user['username']},

Click the link below to verify your email:

{verify_link}

This link expires in 24 hours.
""",
            )
            flash("Verification email sent.")
        except Exception:
            traceback.print_exc()
            flash("Could not send verification email right now.")

        return redirect(url_for("login"))

    return render_template("resend_verification.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password_raw = request.form.get("password", "").strip()

        with get_db() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,),
            ).fetchone()

        if not user:
            flash("User not found.")
            return render_template("login.html")

        if not check_password_hash(user["password"], password_raw):
            flash("Wrong password.")
            return render_template("login.html")

        if user["verified"] == 0:
            flash("Please verify your email first.")
            return redirect(url_for("resend_verification"))

        session["user"] = user["username"]
        session["user_id"] = user["id"]

        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/clips/<video_folder>/<filename>")
def download_clip(video_folder, filename):
    folder_path = os.path.join(app.config["GENERATED_CLIPS_FOLDER"], video_folder)
    return send_from_directory(folder_path, filename, as_attachment=True)


@app.route("/upload", methods=["POST"])
def upload():
    try:
        if "user" not in session:
            return jsonify({"error": "Please log in first."}), 401

        video = request.files.get("video")
        clip_limit_raw = request.form.get("clip_limit", "3")

        if not video or not video.filename:
            return jsonify({"error": "No video file selected."}), 400

        if not allowed_file(video.filename):
            return jsonify({"error": "Unsupported file type."}), 400

        try:
            clip_limit = int(clip_limit_raw)
        except ValueError:
            return jsonify({"error": "Clip limit must be a number."}), 400

        plan = get_user_plan()
        allowed_clip_limit = get_allowed_clip_limit(plan)

        if clip_limit < 1:
            return jsonify({"error": "Clip limit must be at least 1."}), 400

        if clip_limit > allowed_clip_limit:
            return jsonify({"error": f"Your plan allows up to {allowed_clip_limit} clips."}), 400

        safe_name = secure_filename(video.filename)
        unique_prefix = uuid.uuid4().hex[:8]
        stored_name = f"{unique_prefix}_{safe_name}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], stored_name)

        video.save(save_path)

        clips = create_real_clips(save_path, clip_limit)

        return jsonify(
            {
                "plan": plan,
                "uploads_left_today": get_uploads_left_today(plan),
                "allowed_clip_limit": allowed_clip_limit,
                "uploaded_file": stored_name,
                "results": {"clips": clips},
            }
        ), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)