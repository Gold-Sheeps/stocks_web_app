from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import psycopg
import httpx

router = APIRouter(prefix="/alerts", tags=["alerts"])

DB_CONNINFO = "host=localhost port=5432 dbname=postgres user=postgres password=test"


def _ensure_schema():
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS user_alerts (
                    id SERIAL PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    value FLOAT NOT NULL,
                    note TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    current_value FLOAT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)
        conn.commit()


# ─── Models ───────────────────────────────────────────────────────────────────

class AlertCreate(BaseModel):
    symbol: str
    condition: str
    value: float
    note: Optional[str] = ""


class AlertUpdate(BaseModel):
    status: Optional[str] = None
    current_value: Optional[float] = None


class AlertNotifyRequest(BaseModel):
    to_email: str
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str
    smtp_password: str
    use_tls: bool = True
    alerts: list[dict]


class SlackNotifyRequest(BaseModel):
    webhook_url: str
    alerts: list[dict]


# ─── CRUD Endpoints ───────────────────────────────────────────────────────────

@router.get("")
def list_alerts():
    _ensure_schema()
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, condition, value, note, status, current_value,
                       created_at, updated_at
                FROM user_alerts
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "symbol": r[1],
            "condition": r[2],
            "value": r[3],
            "note": r[4] or "",
            "status": r[5],
            "currentValue": r[6],
            "createdAt": r[7].strftime("%Y-%m-%d") if r[7] else None,
            "updatedAt": r[8].isoformat() if r[8] else None,
        }
        for r in rows
    ]


@router.post("")
def create_alert(req: AlertCreate):
    _ensure_schema()
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_alerts (symbol, condition, value, note)
                VALUES (%s, %s, %s, %s)
                RETURNING id
            """, (req.symbol.upper(), req.condition, req.value, req.note or ""))
            row = cur.fetchone()
        conn.commit()
    return {"id": row[0], "success": True}


@router.put("/{alert_id}")
def update_alert(alert_id: int, req: AlertUpdate):
    _ensure_schema()
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            if req.status is not None and req.current_value is not None:
                cur.execute("""
                    UPDATE user_alerts SET status=%s, current_value=%s, updated_at=NOW()
                    WHERE id=%s
                """, (req.status, req.current_value, alert_id))
            elif req.status is not None:
                cur.execute("""
                    UPDATE user_alerts SET status=%s, updated_at=NOW() WHERE id=%s
                """, (req.status, alert_id))
            elif req.current_value is not None:
                cur.execute("""
                    UPDATE user_alerts SET current_value=%s, updated_at=NOW() WHERE id=%s
                """, (req.current_value, alert_id))
        conn.commit()
    return {"success": True}


@router.delete("/{alert_id}")
def delete_alert(alert_id: int):
    _ensure_schema()
    with psycopg.connect(DB_CONNINFO) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_alerts WHERE id=%s", (alert_id,))
        conn.commit()
    return {"success": True}


# ─── Notifications ────────────────────────────────────────────────────────────

@router.post("/notify")
def notify_alerts(req: AlertNotifyRequest):
    from app.services.email_service import EmailService
    return EmailService().send_alert_notification(
        to_email=req.to_email,
        alerts=req.alerts,
        smtp_host=req.smtp_host,
        smtp_port=req.smtp_port,
        smtp_user=req.smtp_user,
        smtp_password=req.smtp_password,
        use_tls=req.use_tls,
    )


@router.post("/notify-slack")
async def notify_slack(req: SlackNotifyRequest):
    CONDITION_LABELS = {
        "price_above": "Price >",
        "price_below": "Price <",
        "rs_above": "RS >",
        "score_above": "Score >",
    }
    lines = [f"*Stock Alert: {len(req.alerts)} alert(s) triggered*\n"]
    for a in req.alerts:
        label = CONDITION_LABELS.get(a.get("condition", ""), a.get("condition", ""))
        target = a.get("value", "")
        current = a.get("currentValue", "--")
        try:
            target_str = f"{float(target):,.4g}"
        except (TypeError, ValueError):
            target_str = str(target)
        try:
            current_str = f"{float(current):,.4g}"
        except (TypeError, ValueError):
            current_str = str(current) if current is not None else "--"
        lines.append(
            f"• `{a.get('symbol', '')}` {label} {target_str} | current: *{current_str}*"
            + (f" — {a.get('note', '')}" if a.get("note") else "")
        )
    payload = {"text": "\n".join(lines)}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(req.webhook_url, json=payload)
        if resp.status_code == 200:
            return {"success": True}
        return {"success": False, "error": f"Slack returned {resp.status_code}: {resp.text}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}
