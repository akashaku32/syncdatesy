"""Branded HTML/plain templates for pipeline notification emails."""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Sequence, Tuple

from django.conf import settings
from django.utils import timezone

# Datasyncher brand palette (matches frontend theme + billing emails)
BRAND_PRIMARY = "#6E2FD5"
BRAND_PRIMARY_DARK = "#5620AB"
BRAND_HEADER_GRADIENT = "linear-gradient(135deg, #894DF0 0%, #5620AB 100%)"
COLOR_BG = "#F4F6FA"
COLOR_CARD = "#FFFFFF"
COLOR_TEXT = "#0F172A"
COLOR_MUTED = "#64748B"
COLOR_BORDER = "#E2E8F0"
COLOR_SUCCESS = "#16A34A"
COLOR_SUCCESS_BG = "#F0FDF4"
COLOR_ERROR = "#DC2626"
COLOR_ERROR_BG = "#FEF2F2"
FONT_STACK = "'Plus Jakarta Sans', 'Helvetica Neue', Arial, sans-serif"


def _email_asset_base_url() -> str:
    base = getattr(settings, "EMAIL_ASSET_BASE_URL", "") or getattr(
        settings, "FRONTEND_URL", ""
    )
    base = str(base).rstrip("/")
    if base:
        return base
    domain = getattr(settings, "DOMAIN", "qa.datasyncher.com")
    return f"https://{domain}"


def _header_image_url() -> str:
    return f"{_email_asset_base_url()}/static/billing/receipt-header.png"


def _format_datetime(value: Any) -> str:
    if not value:
        return "N/A"
    if isinstance(value, str):
        try:
            parsed = timezone.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, timezone.utc)
            return timezone.localtime(parsed).strftime("%b %d, %Y %I:%M %p %Z").strip()
        except (TypeError, ValueError):
            return value
    if hasattr(value, "strftime"):
        try:
            localized = timezone.localtime(value) if timezone.is_aware(value) else value
            return localized.strftime("%b %d, %Y %I:%M %p %Z").strip()
        except (TypeError, ValueError):
            pass
    return str(value)


def _status_tone(status: str) -> Tuple[str, str, str]:
    normalized = (status or "unknown").lower()
    if normalized in {"completed", "success"}:
        return "Completed", COLOR_SUCCESS, COLOR_SUCCESS_BG
    if normalized in {"failed", "error", "timeout"}:
        return normalized.title(), COLOR_ERROR, COLOR_ERROR_BG
    if normalized in {"running", "in_progress"}:
        return "Running", "#2563EB", "#EFF6FF"
    return normalized.title(), COLOR_MUTED, "#F8FAFC"


def _status_badge_html(status: str) -> str:
    label, color, bg = _status_tone(status)
    return (
        f'<span style="display:inline-block;padding:4px 10px;border-radius:999px;'
        f'font-size:12px;font-weight:600;color:{color};background:{bg};">'
        f"{html.escape(label)}</span>"
    )


def _metadata_rows_html(rows: Sequence[Tuple[str, str]]) -> str:
    if not rows:
        return ""
    body_rows = []
    for label, value in rows:
        body_rows.append(
            "<tr>"
            f'<td style="padding:8px 12px 8px 0;font-size:11px;color:#94A3B8;'
            f'text-transform:uppercase;letter-spacing:0.04em;white-space:nowrap;'
            f'vertical-align:top;width:38%;">{html.escape(label)}</td>'
            f'<td style="padding:8px 0;font-size:14px;color:{COLOR_TEXT};'
            f'font-weight:600;vertical-align:top;">{value}</td>'
            "</tr>"
        )
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="background:#F8FAFC;border:1px solid {COLOR_BORDER};border-radius:12px;">'
        "<tr><td style=\"padding:14px 18px;\">"
        '<table width="100%" cellspacing="0" cellpadding="0">'
        f"{''.join(body_rows)}"
        "</table></td></tr></table>"
    )


def _batch_rows_html(batch_rows: List[Dict[str, Any]]) -> str:
    if not batch_rows:
        return (
            f'<p style="margin:0;font-size:13px;color:{COLOR_MUTED};">'
            "No batch details available.</p>"
        )

    header = (
        "<tr>"
        f'<th align="left" style="padding:10px 12px;font-size:11px;color:#94A3B8;'
        f'text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid {COLOR_BORDER};">'
        "Batch</th>"
        f'<th align="left" style="padding:10px 12px;font-size:11px;color:#94A3B8;'
        f'text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid {COLOR_BORDER};">'
        "Status</th>"
        f'<th align="left" style="padding:10px 12px;font-size:11px;color:#94A3B8;'
        f'text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid {COLOR_BORDER};">'
        "Tables</th>"
        f'<th align="left" style="padding:10px 12px;font-size:11px;color:#94A3B8;'
        f'text-transform:uppercase;letter-spacing:0.04em;border-bottom:1px solid {COLOR_BORDER};">'
        "Notes</th>"
        "</tr>"
    )
    body = []
    for row in batch_rows:
        body.append(
            "<tr>"
            f'<td style="padding:12px;font-size:14px;color:{COLOR_TEXT};border-bottom:1px solid #F1F5F9;">'
            f"{html.escape(str(row.get('batch_name') or 'Batch'))}</td>"
            f'<td style="padding:12px;border-bottom:1px solid #F1F5F9;">'
            f"{_status_badge_html(str(row.get('status') or 'unknown'))}</td>"
            f'<td style="padding:12px;font-size:14px;color:{COLOR_TEXT};border-bottom:1px solid #F1F5F9;">'
            f"{html.escape(str(row.get('table_count', 'N/A')))}</td>"
            f'<td style="padding:12px;font-size:13px;color:{COLOR_MUTED};border-bottom:1px solid #F1F5F9;">'
            f"{html.escape(str(row.get('error') or '-'))}</td>"
            "</tr>"
        )
    return (
        '<table width="100%" cellspacing="0" cellpadding="0" '
        f'style="border:1px solid {COLOR_BORDER};border-radius:12px;overflow:hidden;">'
        f"<thead>{header}</thead><tbody>{''.join(body)}</tbody></table>"
    )


def _build_branded_email_html(
    *,
    title: str,
    subtitle: str,
    intro: str,
    status_label: str,
    status_value: str,
    metadata_rows: Sequence[Tuple[str, str]],
    detail_html: str = "",
    footer_note: str = "This is an automated notification from Datasyncher Pipeline.",
) -> str:
    header_url = _header_image_url()
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{html.escape(title)}</title>
</head>
<body style="margin:0;padding:0;background:{COLOR_BG};font-family:{FONT_STACK};color:{COLOR_TEXT};">
  <table width="100%" cellspacing="0" cellpadding="0" style="padding:24px 12px;">
    <tr>
      <td align="center">
        <table width="600" cellspacing="0" cellpadding="0"
               style="max-width:600px;width:100%;background:{COLOR_CARD};border:1px solid #E5E7EB;border-radius:16px;overflow:hidden;">
          <tr>
            <td style="padding:0;background:{BRAND_PRIMARY_DARK};">
              <img src="{html.escape(header_url, quote=True)}" alt="Datasyncher"
                   width="600" style="display:block;border:0;max-width:100%;height:auto;" />
            </td>
          </tr>
          <tr>
            <td style="padding:24px 24px 8px 24px;text-align:center;">
              <div style="font-size:20px;font-weight:700;color:{COLOR_TEXT};">{html.escape(title)}</div>
              <div style="margin-top:6px;font-size:13px;color:{COLOR_MUTED};">{html.escape(subtitle)}</div>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 16px 24px;text-align:center;">
              {_status_badge_html(status_value)}
              <span style="margin-left:8px;font-size:12px;color:{COLOR_MUTED};">{html.escape(status_label)}</span>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 18px 24px;">
              <p style="margin:0;font-size:14px;line-height:1.6;color:#334155;">{html.escape(intro)}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:0 24px 18px 24px;">
              {_metadata_rows_html(metadata_rows)}
            </td>
          </tr>
          {f'<tr><td style="padding:0 24px 20px 24px;">{detail_html}</td></tr>' if detail_html else ''}
          <tr>
            <td style="padding:18px 24px 24px 24px;border-top:1px solid #F1F5F9;text-align:center;">
              <p style="margin:0 0 6px 0;font-size:12px;color:{COLOR_MUTED};">{html.escape(footer_note)}</p>
              <p style="margin:0;font-size:12px;color:#94A3B8;">&copy; Datasyncher</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def parse_batch_rows_from_run_metadata(metadata: dict) -> List[Dict[str, Any]]:
    nodes_meta = metadata.get("nodes") or {}
    rows: List[Dict[str, Any]] = []
    for node_id, node_data in sorted(nodes_meta.items(), key=lambda item: str(item[0])):
        if not isinstance(node_data, dict):
            continue
        rows.append(
            {
                "batch_name": node_data.get("batch_name") or f"Node {node_id}",
                "status": node_data.get("status") or "unknown",
                "table_count": node_data.get("table_count", "N/A"),
                "error": node_data.get("error") or "",
            }
        )
    return rows


def build_pipeline_flow_email(
    *,
    pipeline_name: str,
    run_id: int,
    run_status: str,
    started_at: Any,
    finished_at: Any,
    error_message: Optional[str],
    batch_rows: List[Dict[str, Any]],
) -> Tuple[str, str, str]:
    status_label = (run_status or "unknown").title()
    subject = f'Datasyncher Pipeline — "{pipeline_name}" run #{run_id} {status_label.lower()}'

    plain_batches = []
    for row in batch_rows:
        line = f"- {row.get('batch_name')}: {row.get('status')}"
        if row.get("table_count") not in (None, "N/A"):
            line += f" ({row.get('table_count')} tables)"
        if row.get("error"):
            line += f" — {row.get('error')}"
        plain_batches.append(line)
    batches_plain = "\n".join(plain_batches) if plain_batches else "No batch details available."

    intro = (
        f'Your pipeline "{pipeline_name}" has finished with status {status_label}.'
        if run_status == "completed"
        else f'Your pipeline "{pipeline_name}" ended with status {status_label}.'
    )
    text_body = (
        f"Datasyncher Pipeline Notification\n\n"
        f"Pipeline: {pipeline_name}\n"
        f"Run ID: {run_id}\n"
        f"Status: {status_label}\n"
        f"Started: {_format_datetime(started_at)}\n"
        f"Finished: {_format_datetime(finished_at)}\n"
    )
    if error_message:
        text_body += f"Error: {error_message}\n"
    text_body += f"\nBatches:\n{batches_plain}\n"

    detail_html = ""
    if error_message:
        detail_html += (
            f'<div style="margin:0 0 16px 0;padding:12px 14px;background:{COLOR_ERROR_BG};'
            f'border:1px solid #FECACA;border-radius:10px;">'
            f'<div style="font-size:12px;font-weight:600;color:{COLOR_ERROR};margin-bottom:4px;">Error details</div>'
            f'<div style="font-size:13px;line-height:1.5;color:#7F1D1D;">{html.escape(error_message)}</div>'
            "</div>"
        )
    detail_html += (
        '<div style="font-size:12px;font-weight:600;color:#94A3B8;text-transform:uppercase;'
        'letter-spacing:0.04em;margin-bottom:10px;">Batch summary</div>'
        f"{_batch_rows_html(batch_rows)}"
    )

    html_body = _build_branded_email_html(
        title="Pipeline run update",
        subtitle=f"{pipeline_name} &middot; Run #{run_id}",
        intro=intro,
        status_label="Run status",
        status_value=run_status or "unknown",
        metadata_rows=[
            ("Pipeline", html.escape(pipeline_name)),
            ("Run ID", f"#{run_id}"),
            ("Started", html.escape(_format_datetime(started_at))),
            ("Finished", html.escape(_format_datetime(finished_at))),
        ],
        detail_html=detail_html,
    )
    return subject, text_body, html_body


def build_pipeline_batch_email(
    *,
    pipeline_name: str,
    run_id: int,
    batch_name: str,
    table_count: Any,
    duration: str,
    started_at: Any,
    finished_at: Any,
) -> Tuple[str, str, str]:
    subject = (
        f'Datasyncher Pipeline — batch "{batch_name}" completed '
        f'({pipeline_name}, run #{run_id})'
    )
    intro = (
        f'Batch "{batch_name}" completed successfully in pipeline "{pipeline_name}".'
    )
    text_body = (
        f"Datasyncher Pipeline Notification\n\n"
        f"Pipeline: {pipeline_name}\n"
        f"Run ID: {run_id}\n"
        f"Batch: {batch_name}\n"
        f"Status: Completed\n"
        f"Tables: {table_count if table_count is not None else 'N/A'}\n"
        f"Duration: {duration}\n"
        f"Started: {_format_datetime(started_at)}\n"
        f"Finished: {_format_datetime(finished_at)}\n"
    )
    html_body = _build_branded_email_html(
        title="Batch completed",
        subtitle=f"{pipeline_name} &middot; Run #{run_id}",
        intro=intro,
        status_label="Batch status",
        status_value="completed",
        metadata_rows=[
            ("Pipeline", html.escape(pipeline_name)),
            ("Run ID", f"#{run_id}"),
            ("Batch", html.escape(batch_name)),
            ("Tables", html.escape(str(table_count if table_count is not None else "N/A"))),
            ("Duration", html.escape(duration)),
            ("Started", html.escape(_format_datetime(started_at))),
            ("Finished", html.escape(_format_datetime(finished_at))),
        ],
    )
    return subject, text_body, html_body
