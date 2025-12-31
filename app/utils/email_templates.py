# app/utils/email_templates.py
from datetime import datetime
from typing import Optional


def render_email_template(
    title: str,
    body_html: str,
    cta_text: Optional[str] = None,
    cta_url: Optional[str] = None,
    hospital_name: Optional[str] = None,
) -> str:
    """
    Render a unified HTML email template with header, body, CTA button, and footer.
    """
    app_name = "Hospital Management System"
    if hospital_name:
        header_title = f"{app_name} - {hospital_name}"
    else:
        header_title = app_name

    cta_section = ""
    if cta_text and cta_url:
        cta_section = f"""
        <div style="text-align: center; margin: 30px 0;">
            <a href="{cta_url}" style="
                display: inline-block;
                padding: 12px 30px;
                background-color: #1d7af3;
                color: #ffffff;
                text-decoration: none;
                border-radius: 6px;
                font-weight: 600;
                font-size: 16px;
            ">{cta_text}</a>
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
    </head>
    <body style="
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        line-height: 1.6;
        color: #333333;
        background-color: #f5f7fb;
        margin: 0;
        padding: 0;
    ">
        <table role="presentation" style="width: 100%; max-width: 600px; margin: 0 auto; background-color: #ffffff;">
            <tr>
                <td style="padding: 40px 30px; background-color: #1d7af3; text-align: center;">
                    <h1 style="
                        color: #ffffff;
                        margin: 0;
                        font-size: 24px;
                        font-weight: 600;
                    ">{header_title}</h1>
                </td>
            </tr>
            <tr>
                <td style="padding: 40px 30px;">
                    <h2 style="
                        color: #1d7af3;
                        margin: 0 0 20px 0;
                        font-size: 20px;
                        font-weight: 600;
                    ">{title}</h2>
                    <div style="color: #555555; font-size: 16px;">
                        {body_html}
                    </div>
                    {cta_section}
                </td>
            </tr>
            <tr>
                <td style="
                    padding: 30px;
                    background-color: #f5f7fb;
                    text-align: center;
                    font-size: 12px;
                    color: #888888;
                    border-top: 1px solid #e0e0e0;
                ">
                    <p style="margin: 0 0 10px 0;">
                        © {{{{year}}}} {app_name}. All rights reserved.
                    </p>
                    <p style="margin: 0;">
                        This is an automated message. Please do not reply to this email.
                    </p>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """
    return html.replace("{{year}}", str(datetime.now().year))


def render_registration_email(
    hospital_name: str,
    admin_email: str,
    temp_password: str,
    verification_url: str,
) -> tuple[str, str]:
    """
    Render registration confirmation email.
    Returns (subject, html_body).
    """
    subject = f"Welcome to HMS - {hospital_name} Registration"
    body_html = f"""
    <p>Dear Administrator,</p>
    <p>Your hospital <strong>{hospital_name}</strong> has been successfully registered with the Hospital Management System.</p>
    <p><strong>Your login credentials:</strong></p>
    <ul>
        <li><strong>Email:</strong> {admin_email}</li>
        <li><strong>Temporary Password:</strong> <code style="background-color: #f0f0f0; padding: 2px 6px; border-radius: 3px;">{temp_password}</code></li>
    </ul>
    <p>Please verify your email address to activate your hospital account. After verification, you can log in and start using the system.</p>
    <p><strong>Important:</strong> Please change your password after your first login.</p>
    """
    html = render_email_template(
        title="Hospital Registration Confirmation",
        body_html=body_html,
        cta_text="Verify Email Address",
        cta_url=verification_url,
        hospital_name=hospital_name,
    )
    return subject, html


def render_verification_email(
    hospital_name: str,
    verification_url: str,
) -> tuple[str, str]:
    """
    Render email verification email.
    Returns (subject, html_body).
    """
    subject = f"Verify Your Email - {hospital_name}"
    body_html = f"""
    <p>Dear Administrator,</p>
    <p>Please verify your email address to complete the activation of <strong>{hospital_name}</strong>.</p>
    <p>Click the button below to verify your email address. This link will expire in 24 hours.</p>
    """
    html = render_email_template(
        title="Email Verification Required",
        body_html=body_html,
        cta_text="Verify Email Address",
        cta_url=verification_url,
        hospital_name=hospital_name,
    )
    return subject, html
