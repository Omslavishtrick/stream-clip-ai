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

import stripe
import subprocess
import imageio_ffmpeg
import math
import os
import smtplib
import traceback
import uuid
from pathlib import Path
from email.message import EmailMessage
from dotenv import load_dotenv
load_dotenv()

try:
    from moviepy import VideoFileClip
except ImportError:
    from moviepy.editor import VideoFileClip

from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path("/var/data")

UPLOAD_FOLDER = DATA_DIR / "uploads"
GENERATED_CLIPS_FOLDER = DATA_DIR / "generated_clips"
DATABASE_PATH = DATA_DIR / "users.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
GENERATED_CLIPS_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["GENERATED_CLIPS_FOLDER"] = str(GENERATED_CLIPS_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # 1 GB max upload
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
bcrypt = Bcrypt(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    verified = db.Column(db.Boolean, default=False)
    plan = db.Column(db.String(20), default="free")
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    uploads_today = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

with app.app_context():
    db.create_all()

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID")

stripe.api_key = STRIPE_SECRET_KEY

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
GENERATED_CLIPS_FOLDER.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm", "m4v"}


def get_base_url():
    if BASE_URL:
        return BASE_URL
    return request.url_root.rstrip("/")





def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def get_user_plan():
    user_id = session.get("user_id")
    if not user_id:
        return "free"

    user = User.query.get(user_id)

    if not user:
        return "free"

    return user.plan or "free"


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    return User.query.get(user_id)


def get_allowed_clip_limit(plan):
    return 20 if plan == "premium" else 3


def get_uploads_left_today(plan):
    return 999 if plan == "premium" else 3


def build_verification_email_html(username, verification_link):
    logo_url = f"{get_base_url()}/static/images/logo.png"

    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Verify your email</title>
</head>
<body style="margin:0; padding:0; background-color:#111827; font-family:Arial, sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background-color:#111827; margin:0; padding:30px 15px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:560px; background-color:#1f2937; border-radius:16px; overflow:hidden;">
                    <tr>
                        <td align="center" style="padding:32px 32px 16px 32px;">
                            <img src="{logo_url}" alt="LI3 Media Logo" width="88" style="display:block; margin:0 auto 14px auto;">
                            <h1 style="margin:0; font-size:28px; line-height:1.2; color:#ffffff;">Verify your email</h1>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding:0 32px 10px 32px; color:#d1d5db; font-size:16px; line-height:1.7;">
                            <p style="margin:0 0 16px 0;">Hi {username},</p>
                            <p style="margin:0 0 16px 0;">Thanks for signing up for <strong style="color:#ffffff;">LI3 Media</strong>.</p>
                            <p style="margin:0 0 24px 0;">Please confirm your email address by clicking the button below.</p>
                        </td>
                    </tr>

                    <tr>
                        <td align="center" style="padding:0 32px 10px 32px;">
                            <a href="{verification_link}" style="display:inline-block; background-color:#2563eb; color:#ffffff; text-decoration:none; font-size:16px; font-weight:bold; padding:14px 28px; border-radius:10px;">
                                Verify Account
                            </a>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding:12px 32px 0 32px; color:#9ca3af; font-size:14px; line-height:1.7;">
                            <p style="margin:0 0 10px 0;">This verification link expires in 24 hours.</p>
                            <p style="margin:0 0 10px 0;">If the button does not work, copy and paste this link into your browser:</p>
                            <p style="margin:0 0 18px 0; word-break:break-all;">
                                <a href="{verification_link}" style="color:#93c5fd; text-decoration:none;">{verification_link}</a>
                            </p>
                        </td>
                    </tr>

                    <tr>
                        <td style="padding:0 32px 32px 32px; color:#6b7280; font-size:13px; line-height:1.6;">
                            <p style="margin:0;">If you did not create this account, you can safely ignore this email.</p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""


def send_email(to_email, subject, body, html_body=None):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        raise ValueError("SMTP settings missing. Set SMTP_USERNAME and SMTP_PASSWORD.")
    if not EMAIL_FROM:
        raise ValueError("EMAIL_FROM is missing.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"LI3 Media <{EMAIL_FROM}>"
    msg["To"] = to_email
    msg.set_content(body)

    if html_body:
        msg.add_alternative(html_body, subtype="html")

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


def export_clip_with_audio_ffmpeg(input_path, output_path, start_time, end_time, add_watermark=True):
    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    duration = round(end_time - start_time, 2)

    if duration <= 0:
        raise ValueError("Clip duration must be greater than 0.")

    logo_path = str(BASE_DIR / "static/images/logo.png")

    command = [
        ffmpeg_path,
        "-y",
        "-ss", str(start_time),
        "-i", input_path,
        "-t", str(duration),
    ]

    if add_watermark:
        command += [
            "-i", logo_path,
            "-filter_complex",
            "[1:v]scale=70:-1,format=rgba,colorchannelmixer=aa=0.45[wm];"
            "[0:v][wm]overlay=W-w-12:H-h-12[vout]",
            "-map", "[vout]",
            "-map", "0:a:0?",
        ]
    else:
        command += [
            "-map", "0:v:0",
            "-map", "0:a:0?",
        ]

    command += [
        "-c:v", "libx264",
        "-c:a", "aac",
        "-movflags", "+faststart",
        output_path,
    ]

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed while exporting clip.\nSTDERR:\n{result.stderr}"
        )

def create_real_clips(input_path, clip_limit, plan):
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
            start_time = float(moment["start"])
            end_time = float(moment["end"])

            if end_time <= start_time:
                continue

            output_filename = f"clip_{i}.mp4"
            output_path = os.path.join(output_folder, output_filename)

            export_clip_with_audio_ffmpeg(
                input_path=input_path,
                output_path=output_path,
                start_time=start_time,
                end_time=end_time,
                add_watermark=(plan != "premium")
            )

            clips_data.append(
                {
                    "clip_number": i,
                    "start_time": round(start_time, 2),
                    "end_time": round(end_time, 2),
                    "start_label": format_seconds(start_time),
                    "end_label": format_seconds(end_time),
                    "score": round(moment.get("score", 0.0), 6),
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

        existing_user = User.query.filter(
            (User.username == username) | (User.email == email)
        ).first()

        if existing_user:
            flash("Username or email already exists.")
            return render_template("register.html")

        new_user = User(
            username=username,
            email=email,
            password_hash=hashed_password,
        )

        db.session.add(new_user)
        db.session.commit()

        token = generate_token(email)
        verify_link = f"{get_base_url()}/verify/{token}"

        plain_body = f"""Hi {username},

Thanks for signing up for LI3 Media.

Click the link below to verify your email:

{verify_link}

This link expires in 24 hours.

If you did not create this account, you can ignore this email.
"""
        html_body = build_verification_email_html(username, verify_link)

        try:
            send_email(
                email,
                "Verify your LI3 Media account",
                plain_body,
                html_body=html_body,
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
        return render_template(
            "verify_result.html",
            status="error",
            title="Link Expired",
            message="This verification link has expired. Please request a new verification email.",
        ), 400

    except BadSignature:
        return render_template(
            "verify_result.html",
            status="error",
            title="Invalid Link",
            message="This verification link is invalid. Please request a new verification email.",
        ), 400

    # Look up user in PostgreSQL
    user = User.query.filter_by(email=email).first()

    if not user:
        return render_template(
            "verify_result.html",
            status="error",
            title="Account Not Found",
            message="We could not find an account for this verification link.",
        ), 404

    if user.verified:
        return render_template(
            "verify_result.html",
            status="success",
            title="Already Verified",
            message="Your email is already verified. You can log in to your account now.",
        ), 200

    # Mark account as verified
    user.verified = True
    db.session.commit()

    return render_template(
        "verify_result.html",
        status="success",
        title="Email Verified",
        message="Your email has been verified successfully. You can now log in to your account.",
    ), 200


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Email is required.")
            return render_template("resend_verification.html")

        user = User.query.filter_by(email=email).first()

        if not user:
            flash("No account found with that email.")
            return render_template("resend_verification.html")

        if user.verified:
            flash("That email is already verified. Please log in.")
            return redirect(url_for("login"))

        token = generate_token(user.email)
        verify_link = f"{get_base_url()}/verify/{token}"

        plain_body = f"""Hi {user['username']},

Thanks for signing up for LI3 Media.

Click the link below to verify your email:

{verify_link}

This link expires in 24 hours.

If you did not create this account, you can ignore this email.
"""
        html_body = build_verification_email_html(user["username"], verify_link)

        try:
            send_email(
                user["email"],
                "Verify your LI3 Media account",
                plain_body,
                html_body=html_body,
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

        user = User.query.filter(
            (User.username == username) | (User.email == username)
        ).first()

        if not user:
            flash("User not found.")
            return render_template("login.html")

        if not check_password_hash(user.password_hash, password_raw):
            flash("Wrong password.")
            return render_template("login.html")

        if not user.verified:
            flash("Please verify your email first.")
            return redirect(url_for("resend_verification"))

        session["user"] = user.username
        session["user_id"] = user.id

        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/clips/<video_folder>/<filename>")
def download_clip(video_folder, filename):
    folder_path = os.path.join(app.config["GENERATED_CLIPS_FOLDER"], video_folder)
    return send_from_directory(folder_path, filename, as_attachment=True)


@app.route("/billing/success")
def billing_success():
    if "user" not in session:
        return redirect(url_for("login"))

    return render_template("billing_success.html")


@app.route("/billing/cancel")
def billing_cancel():
    if "user" not in session:
        return redirect(url_for("login"))

    return render_template("billing_cancel.html")


@app.route("/stripe/webhook", methods=["POST"])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get("Stripe-Signature")

    try:
        event = stripe.Webhook.construct_event(
            payload,
            sig_header,
            STRIPE_WEBHOOK_SECRET,
        )
    except ValueError:
        return "Invalid payload", 400
    except stripe.error.SignatureVerificationError:
        return "Invalid signature", 400

    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]

        metadata = session_obj.metadata
        user_id = metadata.user_id if metadata and hasattr(metadata, "user_id") else None

        if not user_id:
            user_id = session_obj.client_reference_id

        customer_id = session_obj.customer
        subscription_id = session_obj.subscription

        user = User.query.get(int(user_id))

        if user:
            user.plan = "premium"
            user.stripe_customer_id = customer_id
            user.stripe_subscription_id = subscription_id
            db.session.commit()

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        subscription_id = subscription.get("id")

        user = User.query.filter_by(stripe_subscription_id=subscription_id).first()

        if user:
            user.plan = "free"
            user.stripe_subscription_id = None
            db.session.commit()

    elif event["type"] == "customer.subscription.updated":
        subscription = event["data"]["object"]
        subscription_id = subscription.get("id")
        status = subscription.get("status")

        new_plan = "premium" if status in ("active", "trialing") else "free"

        user = User.query.filter_by(stripe_subscription_id=subscription_id).first()

        if user:
            user.plan = new_plan
            db.session.commit()

    return "", 200


@app.route("/upgrade", methods=["POST"])
def upgrade():
    if "user_id" not in session:
        return jsonify({"error": "Please log in first."}), 401

    if not STRIPE_SECRET_KEY or not STRIPE_PRICE_ID:
        return jsonify({"error": "Stripe is not configured yet."}), 500

    user = get_current_user()
    if not user:
        return jsonify({"error": "User not found."}), 404

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[
                {
                    "price": STRIPE_PRICE_ID,
                    "quantity": 1,
                }
            ],
            success_url=f"{get_base_url()}/billing/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{get_base_url()}/billing/cancel",
            client_reference_id=str(user.id),
            customer_email=user.email,
            metadata={
                "user_id": str(user.id),
                "username": user.username,
            },
        )

        return jsonify({"checkout_url": checkout_session.url}), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


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

        clips = create_real_clips(save_path, clip_limit, plan)

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




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=True)