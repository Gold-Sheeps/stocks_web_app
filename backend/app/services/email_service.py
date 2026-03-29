import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText


CONDITION_LABELS = {
    "price_above": "Price >",
    "price_below": "Price <",
    "rs_above": "RS >",
    "score_above": "Score >",
}


def _build_html_body(alerts: list[dict]) -> str:
    rows = ""
    for a in alerts:
        symbol = a.get("symbol", "")
        condition = CONDITION_LABELS.get(a.get("condition", ""), a.get("condition", ""))
        target = a.get("value", "")
        current = a.get("currentValue", "")
        try:
            target_str = f"{float(target):,.4g}"
        except (TypeError, ValueError):
            target_str = str(target)
        try:
            current_str = f"{float(current):,.4g}"
        except (TypeError, ValueError):
            current_str = str(current) if current is not None else "--"
        note = a.get("note", "") or ""
        rows += f"""
        <tr>
          <td style="padding:10px 16px; font-family:monospace; font-weight:700; color:#6366f1;">{symbol}</td>
          <td style="padding:10px 16px; color:#94a3b8;">{condition}</td>
          <td style="padding:10px 16px; font-family:monospace;">{target_str}</td>
          <td style="padding:10px 16px; font-family:monospace; color:#ef4444; font-weight:700;">{current_str}</td>
          <td style="padding:10px 16px; color:#94a3b8; font-size:0.88em;">{note}</td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="margin:0; padding:0; background:#0f172a; font-family:Arial,sans-serif; color:#f8fafc;">
  <div style="max-width:640px; margin:32px auto; background:#1e293b; border-radius:12px; overflow:hidden; border:1px solid rgba(255,255,255,0.1);">
    <div style="background:#6366f1; padding:24px 32px;">
      <h1 style="margin:0; font-size:1.4rem; font-weight:700; color:#fff;">Stock Alert Notification</h1>
      <p style="margin:6px 0 0; color:rgba(255,255,255,0.8); font-size:0.9rem;">{len(alerts)} alert(s) triggered</p>
    </div>
    <div style="padding:24px 32px;">
      <table style="width:100%; border-collapse:collapse; border-spacing:0;">
        <thead>
          <tr style="border-bottom:1px solid rgba(255,255,255,0.1);">
            <th style="padding:10px 16px; text-align:left; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; color:#94a3b8;">Symbol</th>
            <th style="padding:10px 16px; text-align:left; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; color:#94a3b8;">Condition</th>
            <th style="padding:10px 16px; text-align:left; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; color:#94a3b8;">Target</th>
            <th style="padding:10px 16px; text-align:left; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; color:#94a3b8;">Current</th>
            <th style="padding:10px 16px; text-align:left; font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em; color:#94a3b8;">Note</th>
          </tr>
        </thead>
        <tbody>{rows}
        </tbody>
      </table>
    </div>
    <div style="padding:16px 32px 24px; border-top:1px solid rgba(255,255,255,0.08); color:#64748b; font-size:0.78rem;">
      This notification was sent by your Stock Trading System. Alerts are managed on the Alerts page.
    </div>
  </div>
</body>
</html>"""
    return html


class EmailService:
    def send_alert_notification(
        self,
        to_email: str,
        alerts: list[dict],
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        use_tls: bool = True,
    ) -> dict:
        """Send email notification for triggered alerts."""
        try:
            subject = f"[Stock Alert] {len(alerts)} alert(s) triggered"
            html_body = _build_html_body(alerts)

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = smtp_user
            msg["To"] = to_email

            plain_lines = [f"Stock Alert: {len(alerts)} alert(s) triggered\n"]
            for a in alerts:
                label = CONDITION_LABELS.get(a.get("condition", ""), a.get("condition", ""))
                plain_lines.append(
                    f"  {a.get('symbol', '')}  {label}  "
                    f"target={a.get('value', '')}  current={a.get('currentValue', '')}"
                )
            plain_body = "\n".join(plain_lines)

            msg.attach(MIMEText(plain_body, "plain"))
            msg.attach(MIMEText(html_body, "html"))

            if use_tls:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(smtp_user, smtp_password)
                    server.sendmail(smtp_user, to_email, msg.as_string())
            else:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                    server.login(smtp_user, smtp_password)
                    server.sendmail(smtp_user, to_email, msg.as_string())

            return {"success": True, "sent_to": to_email}

        except Exception as exc:
            return {"success": False, "error": str(exc)}
