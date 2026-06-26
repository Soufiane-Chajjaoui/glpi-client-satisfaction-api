"""
Send email alerts for critical tickets via SMTP (MailHog).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import polars as pl
from lib.polars_helpers import read_pg

SMTP_HOST = os.getenv("SMTP_HOST", "localhost")
SMTP_PORT = int(os.getenv("SMTP_PORT", "1025"))
MAIL_FROM = os.getenv("ALERT_FROM", "helpdesk@ilemgroup.com")
MAIL_TO = os.getenv("ALERT_TO", "manager@ilemgroup.com")


def send_alerts():
    try:
        df = read_pg("gold_alert.critical_tickets")
        if df.height == 0:
            print("No critical tickets to alert.")
            return
        df = df.filter(pl.col("est_critique") == 1)
    except Exception as e:
        print(f"No critical tickets (table may not exist): {e}")
        return

    if df.height == 0:
        print("No critical tickets to alert.")
        return

    rows = df.to_dicts()
    html_rows = "".join(
        f"""<tr>
            <td>{r['ticket_id']}</td><td>{r.get('client_nom', 'N/A')}</td>
            <td>{r.get('titre', '')[:50]}</td>
            <td>{r.get('age_heures', 0):.1f}</td>
            <td>{r.get('ttr_heures', 0):.1f}</td>
            <td>{r.get('heures_restantes_ttr', 0):.1f}</td>
        </tr>"""
        for r in rows
    )

    subject = f"[ALERTE] {len(rows)} ticket(s) critique(s) - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    html = f"""<html><body>
    <h2>Tickets critiques GLPI</h2>
    <table border="1" cellpadding="6">
        <tr><th>ID</th><th>Client</th><th>Titre</th><th>Âge (h)</th><th>TTR (h)</th><th>Restantes</th></tr>
        {html_rows}
    </table>
    <p><i>Pipeline GLPI Light — {datetime.now()}</i></p>
    </body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM
    msg["To"] = MAIL_TO
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.sendmail(MAIL_FROM, MAIL_TO, msg.as_string())

    print(f"Alert sent to {MAIL_TO}: {subject}")


if __name__ == "__main__":
    send_alerts()
