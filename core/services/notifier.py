import smtplib, os
from email.mime.text import MIMEText

def send_email(to_email: str, subject: str, html: str):
    # Configure via .env (ex.: Gmail App Password)
    host = os.getenv("SMTP_HOST","smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT","587"))
    user = os.getenv("SMTP_USER")
    pwd  = os.getenv("SMTP_PASS")

    msg = MIMEText(html, "html")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_email

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.sendmail(user, [to_email], msg.as_string())

def send_whatsapp_via_provider(phone_e164: str, text: str):
    """
    Placeholder:
    - Integre com Twilio WhatsApp API, Zenvia, Gupshup ou Meta WhatsApp Cloud API.
    - Aqui só deixamos a assinatura.
    """
    print(f"[WA] {phone_e164}: {text}")
