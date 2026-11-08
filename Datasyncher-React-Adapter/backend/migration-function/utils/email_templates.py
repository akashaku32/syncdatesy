"""
Email Templates for DataSyncher Notifications

This module provides HTML and plain text templates for different types of
error notifications in the DataSyncher migration system.
"""

from datetime import datetime
import html
import logging
import posixpath
import re
from typing import Dict, Any, Optional


DEFAULT_SUCCESS_SUBJECT = "The {destination} export for {table} finished successfully."
DEFAULT_BODY_FIELDS = ["service", "connection", "company", "table", "rows", "columns", "path", "timestamp"]
DEFAULT_SECTION_STYLES = {
    "bold": False,
    "italic": False,
    "color": "#000000",
    "font_family": "system-ui",
    "font_size": "12px",
}
DEFAULT_PATH_LABEL = "File Path"
DEFAULT_DESTINATION_NAME = "File Destination"


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _render_template_value(template: Any, context: Dict[str, Any]) -> str:
    template_text = "" if template is None else str(template)
    try:
        return template_text.format_map(_SafeFormatDict(context))
    except Exception:
        return template_text


def _build_success_email_context(
    *,
    table_name: str,
    connection_name: str,
    context: Dict[str, Any],
) -> Dict[str, Any]:
    email_context = dict(context or {})
    email_context.setdefault("table", table_name)
    email_context.setdefault("connection", connection_name)
    email_context.setdefault("service", email_context.get("service_name") or email_context.get("service") or "")
    email_context.setdefault("destination", email_context.get("destination") or email_context.get("destination_name") or DEFAULT_DESTINATION_NAME)
    email_context.setdefault("status", email_context.get("status") or "completed")
    email_context.setdefault("company", email_context.get("company_name") or email_context.get("company") or "")
    email_context.setdefault("rows", email_context.get("rows_exported", 0))
    email_context.setdefault("columns", email_context.get("columns_exported", 0))
    email_context.setdefault(
        "path",
        email_context.get("file_path")
        or email_context.get("sharepoint_path")
        or email_context.get("google_drive_path")
        or "",
    )
    email_context.setdefault(
        "file",
        email_context.get("file_name")
        or posixpath.basename(str(email_context.get("path") or "").rstrip("/"))
        or "",
    )
    email_context.setdefault("file_name", email_context.get("file", ""))
    email_context.setdefault("File", email_context.get("file", ""))
    email_context.setdefault(
        "file_url",
        email_context.get("file_url")
        or email_context.get("sharepoint_url")
        or email_context.get("google_drive_url")
        or "",
    )
    email_context.setdefault("path_label", email_context.get("path_label") or DEFAULT_PATH_LABEL)
    email_context.setdefault("timestamp", email_context.get("completed_at") or datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))
    return email_context


def _render_metadata_rows(body_fields: Optional[list], context: Dict[str, Any]) -> list[tuple[str, str]]:
    field_labels = {
        "service": "Service",
        "connection": "Connection",
        "company": "Company",
        "table": "Table",
        "rows": "Rows Exported",
        "columns": "Columns Exported",
        "path": str(context.get("path_label") or DEFAULT_PATH_LABEL),
        "file": "File",
        "File": "File",
        "timestamp": "Completed At",
        "destination": "Destination",
        "status": "Status",
    }
    fields = body_fields if isinstance(body_fields, list) else DEFAULT_BODY_FIELDS
    rows = []
    for field_name in fields:
        field_key = str(field_name or "").strip()
        if not field_key:
            continue
        label = field_labels.get(field_key, field_key.replace("_", " ").title())
        value = context.get(field_key, "")
        rendered_value = _render_template_value(value, context)
        rows.append((label, rendered_value))
    return rows


def _resolve_section_styles(custom_fields: Dict[str, Any], section_name: str) -> Dict[str, Any]:
    fallback_styles = custom_fields.get("styles") if isinstance(custom_fields.get("styles"), dict) else {}
    section_styles = custom_fields.get(f"{section_name}_styles")
    if not isinstance(section_styles, dict):
        section_styles = {}

    resolved_styles = dict(DEFAULT_SECTION_STYLES)
    resolved_styles.update(fallback_styles)
    resolved_styles.update(section_styles)
    return resolved_styles


def _render_styled_text(text: str, styles: Dict[str, Any]) -> str:
    font_family = str(styles.get("font_family") or DEFAULT_SECTION_STYLES["font_family"]).strip() or DEFAULT_SECTION_STYLES["font_family"]
    color = str(styles.get("color") or DEFAULT_SECTION_STYLES["color"]).strip() or DEFAULT_SECTION_STYLES["color"]
    font_size = str(styles.get("font_size") or DEFAULT_SECTION_STYLES["font_size"]).strip() or DEFAULT_SECTION_STYLES["font_size"]
    font_weight = "bold" if bool(styles.get("bold")) else "normal"
    font_style = "italic" if bool(styles.get("italic")) else "normal"
    escaped_text = html.escape(text).replace("\n", "<br>")
    return (
        f'<span style="font-family:{html.escape(font_family)}; color:{html.escape(color)}; '
        f'font-size:{html.escape(font_size)}; font-weight:{font_weight}; font-style:{font_style};">{escaped_text}</span>'
    )


def _render_path_value(path_text: str, path_url: Optional[str]) -> str:
    escaped_path = html.escape(path_text)
    link_url = str(path_url or "").strip()
    if link_url:
        return (
            f'<a href="{html.escape(link_url)}" target="_blank" rel="noopener noreferrer" '
            f'style="color:#4f46e5; text-decoration:underline;">{escaped_path}</a>'
        )
    return escaped_path


def _render_text_link(
    link_text: str,
    link_url: str,
    *,
    font_family: str,
    color: str,
    font_size: str,
    font_weight: str,
    font_style: str,
) -> str:
    return (
        f'<a href="{html.escape(link_url)}" target="_blank" rel="noopener noreferrer" '
        f'style="font-family:{html.escape(font_family)}; color:{html.escape(color)}; '
        f'font-size:{html.escape(font_size)}; font-weight:{font_weight}; font-style:{font_style}; '
        f'text-decoration:underline;">{html.escape(link_text)}</a>'
    )


def _render_metadata_file_link(path_text: str, path_url: str, styles: Dict[str, Any]) -> str:
    font_family = str(styles.get("font_family") or DEFAULT_SECTION_STYLES["font_family"]).strip() or DEFAULT_SECTION_STYLES["font_family"]
    font_size = str(styles.get("font_size") or DEFAULT_SECTION_STYLES["font_size"]).strip() or DEFAULT_SECTION_STYLES["font_size"]
    font_weight = "bold" if bool(styles.get("bold")) else "normal"
    font_style = "italic" if bool(styles.get("italic")) else "normal"
    return _render_text_link(
        path_text,
        path_url,
        font_family=font_family,
        color="#4f46e5",
        font_size=font_size,
        font_weight=font_weight,
        font_style=font_style,
    )


def _render_body_content_html(body_content: Any, styles: Dict[str, Any], context: Dict[str, Any]) -> str:
    template_text = "" if body_content is None else str(body_content)
    if not template_text:
        return ""

    font_family = str(styles.get("font_family") or DEFAULT_SECTION_STYLES["font_family"]).strip() or DEFAULT_SECTION_STYLES["font_family"]
    color = str(styles.get("color") or DEFAULT_SECTION_STYLES["color"]).strip() or DEFAULT_SECTION_STYLES["color"]
    font_size = str(styles.get("font_size") or DEFAULT_SECTION_STYLES["font_size"]).strip() or DEFAULT_SECTION_STYLES["font_size"]
    font_weight = "bold" if bool(styles.get("bold")) else "normal"
    font_style = "italic" if bool(styles.get("italic")) else "normal"
    rendered_body = _render_template_value(template_text, context)
    path_url = str(context.get("file_url") or "").strip()

    if "{path}" not in template_text or not path_url:
        escaped_text = html.escape(rendered_body).replace("\n", "<br>")
        return (
            f'<p style="margin:0 0 12px 0; font-family:{html.escape(font_family)}; color:{html.escape(color)}; '
            f'font-size:{html.escape(font_size)}; font-weight:{font_weight}; font-style:{font_style};">{escaped_text}</p>'
        )

    rendered_parts = []
    path_text = str(context.get("path") or "").strip() or path_url
    for index, part in enumerate(template_text.split("{path}")):
        if part:
            rendered_parts.append(html.escape(_render_template_value(part, context)).replace("\n", "<br>"))
        if index < template_text.count("{path}"):
            rendered_parts.append(_render_path_value(path_text, path_url))

    rendered_html = "".join(rendered_parts)
    return (
        f'<p style="margin:0 0 12px 0; font-family:{html.escape(font_family)}; color:{html.escape(color)}; '
        f'font-size:{html.escape(font_size)}; font-weight:{font_weight}; font-style:{font_style};">{rendered_html}</p>'
    )


def _safe_email_css_value(value: Any, default: str) -> str:
    """Allow only simple inline CSS tokens supplied by a saved template."""
    candidate = str(value or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", candidate) or re.fullmatch(r"[A-Za-z]{1,30}", candidate):
        return candidate
    return default


def _render_callout_html(callout_content: Any, styles: Dict[str, Any], context: Dict[str, Any]) -> str:
    """Render the optional callout independently of body content."""
    content = _render_template_value(callout_content, context).strip()
    if not content:
        return ""

    resolved = dict(styles or {})
    font_family = html.escape(str(resolved.get("font_family") or "system-ui"))
    font_size = html.escape(str(resolved.get("font_size") or "13px"))
    color = html.escape(_safe_email_css_value(resolved.get("color"), "#222222"))
    background = html.escape(_safe_email_css_value(
        resolved.get("background_color") or resolved.get("fill"), "#ffffff"
    ))
    border_color = html.escape(_safe_email_css_value(
        resolved.get("border_color"), "#7c3aed"
    ))
    border_width = html.escape(str(resolved.get("border_width") or "1px"))
    border_radius = html.escape(str(resolved.get("border_radius") or "8px"))
    padding = html.escape(str(resolved.get("padding") or "12px 14px"))
    weight = "bold" if bool(resolved.get("bold")) else "normal"
    font_style = "italic" if bool(resolved.get("italic")) else "normal"
    return (
        f'<div style="margin:12px 0 16px 0; padding:{padding}; background:{background}; '
        f'border:{border_width} solid {border_color}; border-radius:{border_radius}; '
        f'font-family:{font_family}; font-size:{font_size}; color:{color}; '
        f'font-weight:{weight}; font-style:{font_style};">'
        f'{html.escape(content).replace(chr(10), "<br>")}</div>'
    )


def build_file_destination_success_email(
    *,
    table_name: str,
    connection_name: str,
    context: Dict[str, Any],
    email_custom_fields: Optional[Dict[str, Any]] = None,
) -> tuple[str, str, str]:
    email_context = _build_success_email_context(
        table_name=table_name,
        connection_name=connection_name,
        context=context,
    )
    custom_fields = dict(email_custom_fields or {})

    subject_template = custom_fields.get("subject") or DEFAULT_SUCCESS_SUBJECT
    subject = _render_template_value(subject_template, email_context)

    greeting_name = custom_fields.get("greeting_name")
    greeting_text = _render_template_value(greeting_name, email_context).strip()
    greeting_styles = _resolve_section_styles(custom_fields, "greeting")

    body_content = custom_fields.get("body_content")
    body_paragraph = ""
    body_styles = _resolve_section_styles(custom_fields, "body")
    if body_content not in (None, ""):
        body_paragraph = _render_body_content_html(body_content, body_styles, email_context)

    callout_content = (
        custom_fields.get("callout_content")
        if custom_fields.get("callout_content") not in (None, "")
        else custom_fields.get("callout_text")
    )
    callout_styles = custom_fields.get("callout_styles")
    if not isinstance(callout_styles, dict):
        callout_styles = custom_fields.get("callout_style")
    callout_html = _render_callout_html(callout_content, callout_styles or {}, email_context)

    metadata_rows = _render_metadata_rows(custom_fields.get("body_fields"), email_context)
    metadata_html = ""
    metadata_plain_lines = []
    if metadata_rows:
        metadata_value_font_family = str(body_styles.get("font_family") or DEFAULT_SECTION_STYLES["font_family"]).strip() or DEFAULT_SECTION_STYLES["font_family"]
        metadata_value_font_size = str(body_styles.get("font_size") or DEFAULT_SECTION_STYLES["font_size"]).strip() or DEFAULT_SECTION_STYLES["font_size"]
        metadata_value_color = str(body_styles.get("color") or DEFAULT_SECTION_STYLES["color"]).strip() or DEFAULT_SECTION_STYLES["color"]
        metadata_value_font_weight = "bold" if bool(body_styles.get("bold")) else "normal"
        metadata_value_font_style = "italic" if bool(body_styles.get("italic")) else "normal"
        path_text = str(email_context.get("path") or "").strip() or str(email_context.get("file_url") or "").strip()
        path_url = str(email_context.get("file_url") or "").strip()
        metadata_html_rows = "".join(
            f"<tr>"
            f"<td style=\"padding:3px 12px 3px 0; vertical-align:top; white-space:nowrap; font-family:{html.escape(metadata_value_font_family)}; font-size:{html.escape(metadata_value_font_size)}; font-weight:normal; color:{html.escape(metadata_value_color)};\">{html.escape(label)}</td>"
            f"<td style=\"padding:3px 0; vertical-align:top; font-family:{html.escape(metadata_value_font_family)}; font-size:{html.escape(metadata_value_font_size)}; color:{html.escape(metadata_value_color)}; font-weight:{metadata_value_font_weight}; font-style:{metadata_value_font_style};\">"
            f"{_render_metadata_file_link(path_text, path_url, body_styles) if label == str(email_context.get('path_label') or DEFAULT_PATH_LABEL) and path_url else html.escape(str(value))}"
            f"</td>"
            f"</tr>"
            for label, value in metadata_rows
        )
        metadata_html = (
            '<div style="margin:12px 0 16px 0; padding:12px 14px; background:#fafafa; border-radius:8px;">'
            '<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse; width:100%;">'
            f"{metadata_html_rows}"
            "</table>"
            "</div>"
        )
        metadata_plain_lines = [f"{label}: {value}" for label, value in metadata_rows]

    team_name = custom_fields.get("team_name")
    if team_name is None or not str(team_name).strip():
        sign_off_html = '<p style="margin:16px 0 0 0;">Thanks &amp; Regards,</p>'
        sign_off_plain = "Thanks & Regards,"
    else:
        team_styles = _resolve_section_styles(custom_fields, "team")
        sign_off_html = (
            f'<p style="margin:16px 0 0 0;">{_render_styled_text(_render_template_value(team_name, email_context), team_styles)}</p>'
        )
        sign_off_plain = _render_template_value(team_name, email_context)

    primary_color = _safe_email_css_value(custom_fields.get("primary_color"), "#0d9488")
    header_bg_color = _safe_email_css_value(custom_fields.get("header_bg_color"), primary_color)
    header_subtitle = _render_template_value(
        custom_fields.get("header_subtitle") or "", email_context
    ).strip()
    header_html = (
        f'<tr><td style="padding:10px 20px 6px 20px; color:#475569; '
        f'font-size:11px; font-weight:bold; letter-spacing:.04em;">'
        f'{html.escape(header_subtitle)}</td></tr>'
        f'<tr><td style="height:3px; line-height:3px; padding:0 20px;">'
        f'<div style="height:3px; background:{html.escape(header_bg_color)};"></div>'
        f'</td></tr>'
        if header_subtitle else ""
    )

    button_text = _render_template_value(
        custom_fields.get("cta_button_text") or "", email_context
    ).strip()
    button_url = str(email_context.get("file_url") or "").strip()
    button_align = str(custom_fields.get("button_align") or "center").strip().lower()
    if button_align not in {"left", "center", "right"}:
        button_align = "center"
    button_bg = _safe_email_css_value(custom_fields.get("button_bg_color"), primary_color)
    button_fg = _safe_email_css_value(custom_fields.get("button_text_color"), "#ffffff")
    button_variant = str(custom_fields.get("button_variant") or "solid").strip().lower()
    if button_variant not in {"solid", "outline", "subtle"}:
        button_variant = "solid"
    if button_variant == "solid":
        button_style = f"background:{button_bg}; color:{button_fg}; border:1px solid {button_bg};"
    elif button_variant == "outline":
        button_style = f"background:#ffffff; color:{button_bg}; border:1px solid {button_bg};"
    else:
        button_style = f"background:#f1f5f9; color:{button_bg}; border:1px solid {button_bg};"
    cta_html = (
        f'<div style="text-align:{button_align}; margin:20px 0;">'
        f'<a href="{html.escape(button_url)}" target="_blank" rel="noopener noreferrer" '
        f'style="{button_style} padding:8px 16px; border-radius:4px; text-decoration:none; font-weight:bold; display:inline-block;">'
        f'{html.escape(button_text)}</a></div>'
        if button_text and button_url else ""
    )

    logger.info(
        "[file_destination_email_render] table=%s subject=%r greeting=%r body_present=%s team_present=%s custom_keys=%s",
        table_name,
        subject,
        greeting_text,
        body_content not in (None, ""),
        bool(str(team_name or "").strip()),
        sorted(custom_fields.keys()),
    )
    logger.debug(
        "[file_destination_email_render] table=%s subject_styles=%s greeting_styles=%s body_styles=%s team_styles=%s body_fields=%s",
        table_name,
        sorted(custom_fields.get("subject_styles", {}).keys()) if isinstance(custom_fields.get("subject_styles"), dict) else [],
        sorted(greeting_styles.keys()) if isinstance(greeting_styles, dict) else [],
        sorted(_resolve_section_styles(custom_fields, "body").keys()),
        sorted(_resolve_section_styles(custom_fields, "team").keys()),
        custom_fields.get("body_fields"),
    )

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            html, body {{ margin: 0 !important; padding: 0 !important; background: #ffffff; }}
            body {{ font-family: Arial, sans-serif; line-height: 1.5; color: #222; }}
            table {{ border-collapse: collapse; border-spacing: 0; }}
            .wrapper {{ width: 100%; margin: 0; padding: 0; background: #ffffff; }}
            .container {{ width: 100%; max-width: 600px; margin: 0 auto; padding: 0; }}
            .content {{ padding: 16px 20px; }}
            p {{ margin: 0 0 10px 0; }}
        </style>
    </head>
    <body style="margin:0; padding:0; background:#ffffff;">
        <table role="presentation" class="wrapper" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
                <td align="center" style="padding:0;">
                    <table role="presentation" class="container" width="600" cellpadding="0" cellspacing="0" border="0" style="width:100%; max-width:600px; margin:0 auto;">
                        {header_html}
                        <tr>
                            <td class="content" style="padding:16px 20px;">
                                {f'<p style="margin:0 0 12px 0;">{_render_styled_text(greeting_text, greeting_styles)}</p>' if greeting_text else ''}
                                {body_paragraph}
                                {callout_html}
                                {metadata_html}
                                {cta_html}
                                {sign_off_html}
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

    plain_text_parts = [greeting_text] if greeting_text else []
    if body_content not in (None, ""):
        plain_text_parts.extend([
            _render_template_value(body_content, email_context),
            "",
        ])
    if callout_content not in (None, ""):
        plain_text_parts.extend([
            _render_template_value(callout_content, email_context),
            "",
        ])
    if metadata_plain_lines:
        plain_text_parts.extend(metadata_plain_lines)
        plain_text_parts.append("")
    if button_text and button_url:
        plain_text_parts.append(f"{button_text}: {button_url}")
        plain_text_parts.append("")
    plain_text_parts.append(sign_off_plain)
    plain_text = "\n".join(part for part in plain_text_parts if part is not None)

    return subject, html_content.strip(), plain_text.strip()


def build_sharepoint_success_email(
    *,
    table_name: str,
    connection_name: str,
    context: Dict[str, Any],
    email_custom_fields: Optional[Dict[str, Any]] = None,
) -> tuple[str, str, str]:
    sharepoint_context = dict(context or {})
    sharepoint_context.setdefault("destination", "SharePoint")
    sharepoint_context.setdefault("path_label", "SharePoint Path")
    return build_file_destination_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context=sharepoint_context,
        email_custom_fields=email_custom_fields,
    )


def build_google_drive_success_email(
    *,
    table_name: str,
    connection_name: str,
    context: Dict[str, Any],
    email_custom_fields: Optional[Dict[str, Any]] = None,
) -> tuple[str, str, str]:
    google_drive_context = dict(context or {})
    google_drive_context.setdefault("destination", "Google Drive")
    google_drive_context.setdefault("path_label", "Google Drive Path")
    return build_file_destination_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context=google_drive_context,
        email_custom_fields=email_custom_fields,
    )


def create_connection_failure_email(
    connection_type: str,
    connection_name: str,
    error_message: str,
    context: Dict[str, Any]
) -> tuple[str, str]:
    """
    Create email template for connection failures (before any tables start).
    
    Args:
        connection_type: "Salesforce" or "Snowflake"
        connection_name: Name of the connection
        error_message: The actual error message
        context: Additional context (job_name, user, etc.)
        
    Returns:
        Tuple of (html_content, plain_text)
    """
    job_name = context.get("job_name", "Unknown")
    company_name = context.get("company_name", "Unknown Company")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # HTML Template
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
                      color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
            .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
            .error-box {{ background: #fee; border-left: 4px solid #c33; padding: 15px; margin: 20px 0; }}
            .info-box {{ background: #e8f4f8; border-left: 4px solid #0969da; padding: 15px; margin: 20px 0; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #666; }}
            h1 {{ margin: 0; font-size: 24px; }}
            h2 {{ color: #c33; margin-top: 0; }}
            .label {{ font-weight: bold; color: #555; }}
            .value {{ color: #000; }}
            ul {{ padding-left: 20px; }}
            .important {{ background: #fff3cd; border: 1px solid #ffc107; padding: 10px; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🔴 Connection Failure Alert</h1>
                <p style="margin: 10px 0 0 0;">Migration could not start - Connection issue detected</p>
            </div>
            
            <div class="content">
                <h2>❌ {connection_type} Connection Failed</h2>
                
                <div class="important">
                    <strong>⚠️ IMPORTANT:</strong> The migration could not start because the connection to {connection_type} failed.
                    No tables were processed.
                </div>
                
                <div class="info-box">
                    <p><span class="label">Connection:</span> <span class="value">{connection_name}</span></p>
                    <p><span class="label">Company:</span> <span class="value">{company_name}</span></p>
                    <p><span class="label">Job Name:</span> <span class="value">{job_name}</span></p>
                    <p><span class="label">Timestamp:</span> <span class="value">{timestamp}</span></p>
                </div>
                
                <div class="error-box">
                    <h3 style="margin-top: 0;">Error Details:</h3>
                    <pre style="white-space: pre-wrap; word-wrap: break-word; font-family: 'Courier New', monospace; font-size: 13px;">{error_message}</pre>
                </div>
                
                <h3>🔧 Possible Causes:</h3>
                <ul>
                    <li><strong>Authentication Issues:</strong> Access token or credentials expired</li>
                    <li><strong>Network Problems:</strong> Firewall blocking connection or DNS issues</li>
                    <li><strong>Service Unavailable:</strong> {connection_type} service may be temporarily down</li>
                    <li><strong>Configuration Errors:</strong> Incorrect account, warehouse, or schema settings</li>
                    <li><strong>Permission Issues:</strong> Insufficient permissions for the user/role</li>
                </ul>
                
                <h3>📋 Recommended Actions:</h3>
                <ol>
                    <li><strong>Verify Credentials:</strong> Check if {connection_type} credentials are still valid</li>
                    <li><strong>Test Connection:</strong> Try connecting manually through the DataSyncher UI</li>
                    <li><strong>Check Service Status:</strong> Verify {connection_type} service is available</li>
                    <li><strong>Review Permissions:</strong> Ensure the account has necessary permissions</li>
                    <li><strong>Contact Support:</strong> If issue persists, contact DataSyncher support</li>
                </ol>
                
                <div class="info-box" style="margin-top: 20px;">
                    <p style="margin: 0;"><strong>ℹ️ Note:</strong> No tables were migrated because the connection failed before processing started. 
                    Once the connection issue is resolved, you can retry the migration.</p>
                </div>
            </div>
            
            <div class="footer">
                <p>This is an automated notification from DataSyncher Migration System</p>
                <p>For support, please contact your system administrator</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Plain Text Template
    plain_text = f"""
╔═══════════════════════════════════════════════════════════════╗
║          🔴 CONNECTION FAILURE ALERT                          ║
║  Migration Could Not Start - Connection Issue Detected        ║
╚═══════════════════════════════════════════════════════════════╝

❌ {connection_type} CONNECTION FAILED

⚠️ IMPORTANT: The migration could not start because the connection to 
{connection_type} failed. No tables were processed.

CONNECTION DETAILS:
───────────────────────────────────────────────────────────────
Connection:  {connection_name}
Company:     {company_name}
Job Name:    {job_name}
Timestamp:   {timestamp}

ERROR DETAILS:
───────────────────────────────────────────────────────────────
{error_message}

🔧 POSSIBLE CAUSES:
───────────────────────────────────────────────────────────────
• Authentication Issues: Access token or credentials expired
• Network Problems: Firewall blocking connection or DNS issues  
• Service Unavailable: {connection_type} service may be temporarily down
• Configuration Errors: Incorrect account, warehouse, or schema settings
• Permission Issues: Insufficient permissions for the user/role

📋 RECOMMENDED ACTIONS:
───────────────────────────────────────────────────────────────
1. Verify Credentials: Check if {connection_type} credentials are still valid
2. Test Connection: Try connecting manually through the DataSyncher UI
3. Check Service Status: Verify {connection_type} service is available
4. Review Permissions: Ensure the account has necessary permissions
5. Contact Support: If issue persists, contact DataSyncher support

ℹ️ NOTE: No tables were migrated because the connection failed before 
processing started. Once the connection issue is resolved, you can retry 
the migration.

───────────────────────────────────────────────────────────────
This is an automated notification from DataSyncher Migration System
For support, please contact your system administrator
    """
    
    return html_content.strip(), plain_text.strip()


def create_table_migration_failure_email(
    table_name: str,
    error_message: str,
    connection_name: str,
    service_name: str,
    context: Dict[str, Any]
) -> tuple[str, str]:
    """
    Create email template for individual table migration failures.
    
    Args:
        table_name: Name of the failed table
        error_message: The error message
        connection_name: Name of the connection
        service_name: Service name (e.g., "Salesforce to Snowflake")
        context: Additional context
        
    Returns:
        Tuple of (html_content, plain_text)
    """
    job_name = context.get("job_name", "Unknown")
    company_name = context.get("company_name", "Unknown Company")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # HTML Template
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
            .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
            .header {{ background: linear-gradient(135deg, #fc4a1a 0%, #f7b733 100%); 
                      color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
            .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
            .error-box {{ background: #fee; border-left: 4px solid #c33; padding: 15px; margin: 20px 0; }}
            .info-box {{ background: #e8f4f8; border-left: 4px solid #0969da; padding: 15px; margin: 20px 0; }}
            h1 {{ margin: 0; font-size: 24px; }}
            h2 {{ color: #c33; margin-top: 0; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>⚠️ Table Migration Failure</h1>
            </div>
            
            <div class="content">
                <h2>❌ Table: {table_name}</h2>
                
                <div class="info-box">
                    <p><strong>Service:</strong> {service_name}</p>
                    <p><strong>Connection:</strong> {connection_name}</p>
                    <p><strong>Company:</strong> {company_name}</p>
                    <p><strong>Job:</strong> {job_name}</p>
                    <p><strong>Time:</strong> {timestamp}</p>
                </div>
                
                <div class="error-box">
                    <h3>Error Details:</h3>
                    <pre style="white-space: pre-wrap;">{error_message}</pre>
                </div>
            </div>
        </div>
    </body>
    </html>
    """
    
    plain_text = f"""
╔═══════════════════════════════════════════════════════════════╗
║             ⚠️ TABLE MIGRATION FAILURE                        ║
╚═══════════════════════════════════════════════════════════════╝

❌ Table: {table_name}

Service:     {service_name}
Connection:  {connection_name}
Company:     {company_name}
Job:         {job_name}
Timestamp:   {timestamp}

ERROR DETAILS:
───────────────────────────────────────────────────────────────
{error_message}
    """
    
    return html_content.strip(), plain_text.strip()


def create_sharepoint_success_email(
    table_name: str,
    connection_name: str,
    context: Dict[str, Any],
    email_custom_fields: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    """
    Create email template for successful SharePoint table exports.

    Args:
        table_name: Name of the exported table
        connection_name: Name of the connection
        context: Additional context

    Returns:
        Tuple of (html_content, plain_text)
    """
    _, html_content, plain_text = build_sharepoint_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context=context,
        email_custom_fields=email_custom_fields,
    )
    return html_content, plain_text


def create_google_drive_success_email(
    table_name: str,
    connection_name: str,
    context: Dict[str, Any],
    email_custom_fields: Optional[Dict[str, Any]] = None,
) -> tuple[str, str]:
    """
    Create email template for successful Google Drive table exports.

    Args:
        table_name: Name of the exported table
        connection_name: Name of the connection
        context: Additional context

    Returns:
        Tuple of (html_content, plain_text)
    """
    _, html_content, plain_text = build_google_drive_success_email(
        table_name=table_name,
        connection_name=connection_name,
        context=context,
        email_custom_fields=email_custom_fields,
    )
    return html_content, plain_text

logger = logging.getLogger(__name__)
