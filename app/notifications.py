# =============================================================================
# app/notifications.py
# Email notification system using Python's built-in smtplib.
# Sends via Gmail SMTP (MECHggofficial@gmail.com).
#
# SETUP: Add to Replit Secrets:
#   SMTP_USER = MECHggofficial@gmail.com
#   SMTP_PASS = your-gmail-app-password (16-char app password, not account password)
#
# Gmail app password: Google Account → Security → 2FA → App Passwords
#
# All sends are fire-and-forget in a background thread so they never
# block the API response.
# =============================================================================

import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone
from typing import Optional
import os

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = os.getenv("SMTP_USER", "MECHggofficial@gmail.com")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_NAME = "MECHgg"
BASE_URL  = os.getenv("BASE_URL", "https://mechgg.gg")


def _send(to: str, subject: str, html: str, text: str):
    """Send email in background thread. Never raises."""
    if not SMTP_PASS:
        print(f"[NOTIFY] SMTP not configured — would send to {to}: {subject}")
        return

    def _worker():
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{FROM_NAME} <{SMTP_USER}>"
            msg["To"]      = to
            msg.attach(MIMEText(text, "plain"))
            msg.attach(MIMEText(html,  "html"))
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
                s.sendmail(SMTP_USER, to, msg.as_string())
            print(f"[NOTIFY] Sent '{subject}' to {to}")
        except Exception as e:
            print(f"[NOTIFY] Failed to send to {to}: {e}")

    threading.Thread(target=_worker, daemon=True).start()


def _base_html(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ margin:0; padding:0; background:#050608; font-family:'Arial',sans-serif; color:#e2e8f0; }}
  .wrap {{ max-width:560px; margin:0 auto; padding:2rem 1.5rem; }}
  .logo {{ font-size:1.8rem; font-weight:900; letter-spacing:0.1em; color:#f0a500; margin-bottom:2rem; }}
  .logo span {{ color:#e2e8f0; }}
  .card {{ background:#0d1117; border:1px solid #1e2530; border-radius:8px; padding:2rem; margin-bottom:1.5rem; }}
  .title {{ font-size:1.4rem; font-weight:700; color:#f1f5f9; margin:0 0 0.75rem; }}
  p {{ color:#94a3b8; line-height:1.6; margin:0 0 1rem; font-size:0.95rem; }}
  .btn {{ display:inline-block; background:#f0a500; color:#050608; padding:0.75rem 2rem;
          border-radius:4px; text-decoration:none; font-weight:700; font-size:0.9rem;
          letter-spacing:0.05em; margin-top:0.5rem; }}
  .stat {{ font-size:2.5rem; font-weight:900; color:#f0a500; }}
  .label {{ font-size:0.75rem; letter-spacing:0.1em; color:#64748b; text-transform:uppercase; }}
  .footer {{ font-size:0.75rem; color:#374151; margin-top:2rem; text-align:center; line-height:1.6; }}
  .divider {{ border:none; border-top:1px solid #1e2530; margin:1.5rem 0; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">MECH<span>.GG</span></div>
  <div class="card">
    <div class="title">{title}</div>
    {body}
  </div>
  <div class="footer">
    MECHgg · FPS Skill Certification Platform<br>
    Not affiliated with any game developer or publisher.<br>
    <a href="{BASE_URL}" style="color:#f0a500;text-decoration:none">{BASE_URL}</a>
  </div>
</div>
</body>
</html>"""


# =============================================================================
# Notification functions — call these from routes
# =============================================================================

def notify_welcome(to: str, username: str):
    """Sent on successful registration."""
    html = _base_html("Welcome to MECHgg", f"""
        <p>Hey <strong style="color:#f1f5f9">{username}</strong>, you're in.</p>
        <p>Upload your first gameplay clip and get your OSI score, habit report, and rank within minutes.</p>
        <p>Your first analysis is free. No credit card required.</p>
        <a href="{BASE_URL}/#/upload" class="btn">UPLOAD YOUR FIRST CLIP →</a>
        <hr class="divider">
        <p style="font-size:0.85rem">You need 5 sessions to receive your official rank and role suggestion.</p>
    """)
    text = f"Welcome to MECHgg, {username}. Upload your first clip at {BASE_URL}/#/upload"
    _send(to, "Welcome to MECHgg — Upload your first clip", html, text)


def notify_analysis_complete(to: str, username: str, osi: float, rank_label: str, session_count: int):
    """Sent when an analysis session completes."""
    rank_color = "#f0a500" if "Gold" in rank_label else "#6366f1" if "Diamond" in rank_label else "#0ea5e9" if "Platinum" in rank_label else "#b45309" if "Bronze" in rank_label else "#9ca3af"
    sessions_remaining = max(0, 5 - session_count)
    html = _base_html("Analysis Complete", f"""
        <p>Your session has been analysed, <strong style="color:#f1f5f9">{username}</strong>.</p>
        <div style="text-align:center;padding:1.5rem 0">
          <div class="stat">{int(osi)}</div>
          <div class="label">OSI Score</div>
          <div style="margin-top:0.75rem;font-size:1.1rem;font-weight:700;color:{rank_color}">{rank_label}</div>
        </div>
        {'<p style="text-align:center;color:#64748b;font-size:0.85rem">Upload ' + str(sessions_remaining) + ' more session' + ('s' if sessions_remaining != 1 else '') + ' to unlock your official rank.</p>' if sessions_remaining > 0 else '<p style="text-align:center;color:#22c55e;font-size:0.85rem">✓ You have your official rank.</p>'}
        <a href="{BASE_URL}/#/rank" class="btn">VIEW YOUR RANK →</a>
    """)
    text = f"Analysis complete. OSI: {int(osi)} | Rank: {rank_label}. View at {BASE_URL}/#/rank"
    _send(to, f"MECHgg — Analysis Complete | OSI {int(osi)}", html, text)


def notify_rank_up(to: str, username: str, old_rank: str, new_rank: str, osi: float):
    """Sent when a player achieves a new rank tier."""
    html = _base_html("🏆 Rank Up!", f"""
        <p>Congratulations <strong style="color:#f1f5f9">{username}</strong> — you've ranked up!</p>
        <div style="display:flex;align-items:center;justify-content:center;gap:1.5rem;padding:1.5rem 0;text-align:center">
          <div>
            <div style="font-size:1.1rem;color:#64748b;text-decoration:line-through">{old_rank}</div>
            <div class="label" style="margin-top:0.25rem">Previous</div>
          </div>
          <div style="font-size:2rem;color:#f0a500">→</div>
          <div>
            <div style="font-size:1.5rem;font-weight:900;color:#f0a500">{new_rank}</div>
            <div class="label" style="margin-top:0.25rem">New Rank</div>
          </div>
        </div>
        <div style="text-align:center">
          <div class="stat">{int(osi)}</div>
          <div class="label">OSI Score</div>
        </div>
        <a href="{BASE_URL}/#/rank" class="btn" style="display:block;text-align:center;margin-top:1.5rem">VIEW YOUR RANK →</a>
    """)
    text = f"Rank up! {old_rank} → {new_rank} | OSI {int(osi)}. View at {BASE_URL}/#/rank"
    _send(to, f"MECHgg — Rank Up! You're now {new_rank}", html, text)


def notify_booking_received(to: str, coach_username: str, player_username: str, message: str):
    """Sent to coach when they receive a booking request."""
    html = _base_html("New Coaching Request", f"""
        <p>You have a new coaching request from <strong style="color:#f1f5f9">{player_username}</strong>.</p>
        <div style="background:#0a0f16;border:1px solid #1e2530;border-radius:6px;padding:1rem;margin:1rem 0">
          <div class="label" style="margin-bottom:0.5rem">Their message</div>
          <p style="color:#e2e8f0;font-style:italic">"{message[:300]}{'...' if len(message)>300 else ''}"</p>
        </div>
        <p>Accept or decline from your bookings page.</p>
        <a href="{BASE_URL}/#/bookings" class="btn">VIEW BOOKING →</a>
    """)
    text = f"New coaching request from {player_username}: {message[:100]}. View at {BASE_URL}/#/bookings"
    _send(to, f"MECHgg — Coaching Request from {player_username}", html, text)


def notify_booking_accepted(to: str, player_username: str, coach_username: str):
    """Sent to player when coach accepts their booking."""
    html = _base_html("Booking Accepted!", f"""
        <p>Great news, <strong style="color:#f1f5f9">{player_username}</strong>!</p>
        <p><strong style="color:#f0a500">{coach_username}</strong> has accepted your coaching request.</p>
        <p>Head to your messages to coordinate your session details.</p>
        <a href="{BASE_URL}/#/messages" class="btn">OPEN MESSAGES →</a>
    """)
    text = f"{coach_username} accepted your coaching request. Message them at {BASE_URL}/#/messages"
    _send(to, f"MECHgg — {coach_username} accepted your booking", html, text)


def notify_booking_declined(to: str, player_username: str, coach_username: str):
    """Sent to player when coach declines their booking."""
    html = _base_html("Booking Update", f"""
        <p>Hi <strong style="color:#f1f5f9">{player_username}</strong>,</p>
        <p><strong style="color:#94a3b8">{coach_username}</strong> is unable to take your session at this time.</p>
        <p>Browse other available coaches and find one that fits your schedule.</p>
        <a href="{BASE_URL}/#/coaches" class="btn">BROWSE COACHES →</a>
    """)
    text = f"{coach_username} declined your booking. Browse other coaches at {BASE_URL}/#/coaches"
    _send(to, f"MECHgg — Booking Update from {coach_username}", html, text)


def notify_new_message(to: str, recipient_username: str, sender_username: str):
    """Sent when a new message is received."""
    html = _base_html("New Message", f"""
        <p>Hi <strong style="color:#f1f5f9">{recipient_username}</strong>,</p>
        <p>You have a new message from <strong style="color:#f0a500">{sender_username}</strong>.</p>
        <a href="{BASE_URL}/#/messages" class="btn">READ MESSAGE →</a>
    """)
    text = f"New message from {sender_username}. View at {BASE_URL}/#/messages"
    _send(to, f"MECHgg — New message from {sender_username}", html, text)


def notify_competition_winner(to: str, username: str, competition_title: str, prize: float, osi: float):
    """Sent to competition winner."""
    html = _base_html("🏆 You Won!", f"""
        <p>Congratulations <strong style="color:#f1f5f9">{username}</strong> — you won the competition!</p>
        <div style="text-align:center;padding:2rem 0">
          <div style="font-size:1.1rem;color:#94a3b8;margin-bottom:0.5rem">{competition_title}</div>
          <div class="stat">${prize:,.0f}</div>
          <div class="label">Prize Pool</div>
          <div style="margin-top:1rem;color:#94a3b8;font-size:0.9rem">Winning OSI: {int(osi)}</div>
        </div>
        <p>We'll be in touch at this email to arrange your prize payment. Reply to this email to confirm your details.</p>
        <a href="{BASE_URL}/#/competition" class="btn">VIEW RESULTS →</a>
    """)
    text = f"You won {competition_title}! Prize: ${prize:,.0f}. Reply to this email to claim."
    _send(to, f"🏆 MECHgg — You won ${prize:,.0f}!", html, text)


def notify_password_reset(to: str, username: str, reset_token: str):
    """Sent for password reset requests."""
    reset_url = f"{BASE_URL}/#/reset-password?token={reset_token}"
    html = _base_html("Password Reset", f"""
        <p>Hi <strong style="color:#f1f5f9">{username}</strong>,</p>
        <p>We received a request to reset your password. Click below to set a new one.</p>
        <p>This link expires in 1 hour.</p>
        <a href="{reset_url}" class="btn">RESET PASSWORD →</a>
        <hr class="divider">
        <p style="font-size:0.8rem;color:#64748b">If you didn't request this, ignore this email. Your password won't change.</p>
    """)
    text = f"Reset your MECHgg password: {reset_url}"
    _send(to, "MECHgg — Password Reset Request", html, text)
