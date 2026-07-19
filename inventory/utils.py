"""Utility helpers: low-stock email alerts and audit logging."""
from django.conf import settings
from django.core.mail import send_mail

from .models import AuditLog


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
