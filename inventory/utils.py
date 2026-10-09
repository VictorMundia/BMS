"""Utility helpers: low-stock email alerts, SMS and audit logging."""
import json
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail

from .models import AuditLog

AT_LIVE_URL = 'https://api.africastalking.com/version1/messaging'
AT_SANDBOX_URL = 'https://api.sandbox.africastalking.com/version1/messaging'
AT_OK_STATUS = {100, 101, 102}  # processed, sent, queued


class SMSError(Exception):
    pass


def send_sms(message, recipients):
    """Send `message` to `recipients` via Africa's Talking. Returns per-recipient results."""
    username, api_key = settings.AT_USERNAME, settings.AT_API_KEY
    if not username or not api_key:
        raise SMSError('SMS is not configured: set AT_USERNAME and AT_API_KEY.')
    if not recipients:
        raise SMSError('No recipients: set SMS_RECIPIENTS.')

    data = {'username': username, 'to': ','.join(recipients), 'message': message}
    if settings.AT_SENDER_ID:
        data['from'] = settings.AT_SENDER_ID
    request = urllib.request.Request(
        AT_SANDBOX_URL if username == 'sandbox' else AT_LIVE_URL,
        data=urllib.parse.urlencode(data).encode(),
        headers={
            'apiKey': api_key,
            'Accept': 'application/json',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        raise SMSError(f"Africa's Talking rejected the request (HTTP {e.code}).") from e
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SMSError(f"Could not reach Africa's Talking: {e}") from e

    sms_data = body.get('SMSMessageData', {})
    results = sms_data.get('Recipients', [])
    if not results:
        raise SMSError(f"No message sent: {sms_data.get('Message', 'unknown response')}")
    failed = [r for r in results if r.get('statusCode') not in AT_OK_STATUS]
    if failed:
        details = ', '.join(f"{r.get('number')}: {r.get('status')}" for r in failed)
        raise SMSError(f'Failed for {details}')
    return results


def get_client_ip(request):
    """Extract the client IP address from the request."""
    if request is None:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def send_low_stock_alert(product):
    """Email the configured recipient when a product hits its low-stock level."""
    subject = f"Low Stock Alert: {product.name}"
    message = (
        f"The following product is low on stock:\n\n"
        f"Product: {product.name}\n"
        f"Butchery: {product.butchery.name}\n"
        f"Current Stock: {product.current_stock} {product.get_unit_display()}\n"
        f"Minimum Stock: {product.minimum_stock} {product.get_unit_display()}\n\n"
        f"Please restock as soon as possible."
    )
    recipient = getattr(settings, 'LOW_STOCK_ALERT_EMAIL', None)
    if not recipient:
        return
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [recipient],
        fail_silently=True,
    )


def log_action(user, action, model_name, object_id, description, request=None):
    """Create an AuditLog entry capturing the action and originating IP."""
    return AuditLog.objects.create(
        user=user if (user and user.is_authenticated) else None,
        action=action,
        model_name=model_name,
        object_id=object_id or 0,
        description=description,
        ip_address=get_client_ip(request),
    )
