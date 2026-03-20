from flask import Flask, request, render_template, redirect, url_for, flash, session
import sqlite3
import smtplib
import os
import traceback
from email.message import EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = Flask(__name__)

# ======================
# ENVIRONMENT VARIABLES
# ======================
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret-key")

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))

# Use ONE clear name everywhere
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")

# Sender email defaults to SMTP username
EMAIL_FROM = os.environ.get("EMAIL_FROM", SMTP_USERNAME)

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
DATABASE_PATH = os.environ.get("DATABASE_PATH", "users.db")


# ======================
# DATABASE
# ======================
def get_db():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            verified INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ======================
# EMAIL FUNCTIONS
# ======================
def send_email(to_email, subject, body):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        raise ValueError("SMTP settings missing. Set SMTP_USERNAME and SMTP_PASSWORD.")

    if not EMAIL_FROM:
        raise ValueError("EMAIL_FROM is missing.")

    print("ATTEMPTING TO SEND EMAIL TO:", to_email)
    print("SMTP_HOST:", SMTP_HOST)
    print("SMTP_PORT:", SMTP_PORT)
    print("SMTP_USERNAME:", SMTP_USERNAME)
    print("EMAIL_FROM:", EMAIL_FROM)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to_email
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.set_debuglevel(1)  # shows SMTP conversation in Render logs
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)

        print("EMAIL SENT SUCCESSFULLY")

    except Exception as e:
        print("SEND_EMAIL ERROR:", str(e))
        traceback.print_exc()
        raise


# ======================
# TOKEN FUNCTIONS
# ======================
def get_serializer():
    return URLSafeTimedSerializer(app.secret_key)


def generate_token(email):
    return get_serializer().dumps(email, salt="email-verify")


def confirm_token(token):
    return get_serializer().loads(token, salt="email-verify", max_age=86400)


# ======================
# ROUTES
# ======================
@app.route("/")
def home():
    if "user" in session:
        return render_template("index.html", username=session["user"])
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

        conn = get_db()
        existing_user = conn.execute(
            "SELECT id FROM users WHERE username = ? OR email = ?",
            (username, email)
        ).fetchone()

        if existing_user:
            conn.close()
            flash("Username or email already exists.")
            return render_template("register.html")

        conn.execute(
            "INSERT INTO users (username, email, password, verified) VALUES (?, ?, ?, ?)",
            (username, email, hashed_password, 0)
        )
        conn.commit()
        conn.close()

        token = generate_token(email)
        verify_link = f"{BASE_URL}/verify/{token}"

        try:
            send_email(
                email,
                "Verify your email",
                f"""Hi {username},

Thanks for signing up.

Click the link below to verify your email:

{verify_link}

This link expires in 24 hours.
"""
            )
            flash("Account created. Check your email to verify your account.")
        except Exception as e:
            print("REGISTER EMAIL SEND ERROR:", str(e))
            traceback.print_exc()
            flash("Account created, but the verification email could not be sent.")

        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/verify/<token>")
def verify(token):
    try:
        email = confirm_token(token)
    except SignatureExpired:
        return "This verification link expired."
    except BadSignature:
        return "Invalid verification link."

    conn = get_db()
    user = conn.execute(
        "SELECT id, verified FROM users WHERE email = ?",
        (email,)
    ).fetchone()

    if not user:
        conn.close()
        return "Account not found."

    if user["verified"] == 1:
        conn.close()
        return "Email already verified. You can log in."

    conn.execute(
        "UPDATE users SET verified = 1 WHERE email = ?",
        (email,)
    )
    conn.commit()
    conn.close()

    return "Email verified! You can now log in."


@app.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()

        if not email:
            flash("Email is required.")
            return render_template("resend_verification.html")

        conn = get_db()
        user = conn.execute(
            "SELECT username, email, verified FROM users WHERE email = ?",
            (email,)
        ).fetchone()
        conn.close()

        if not user:
            flash("No account found with that email.")
            return render_template("resend_verification.html")

        if user["verified"] == 1:
            flash("That email is already verified. Please log in.")
            return redirect(url_for("login"))

        token = generate_token(user["email"])
        verify_link = f"{BASE_URL}/verify/{token}"

        try:
            send_email(
                user["email"],
                "Verify your email",
                f"""Hi {user["username"]},

Click the link below to verify your email:

{verify_link}

This link expires in 24 hours.
"""
            )
            flash("Verification email sent.")
        except Exception as e:
            print("RESEND EMAIL ERROR:", str(e))
            traceback.print_exc()
            flash("Could not send verification email right now.")

        return redirect(url_for("login"))

    return render_template("resend_verification.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password_raw = request.form.get("password", "").strip()

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        conn.close()

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


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# Run DB setup on startup
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)