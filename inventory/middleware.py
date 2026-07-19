from django.shortcuts import redirect
from django.urls import reverse


class InventoryAccessMiddleware:
    """Role-based access control.

    - Owner (superuser): full access to every page.
    - Butcher (any other authenticated user): may ONLY use the daily stock
      entry page and log out. Any other page redirects back to the entry page.
    - Unauthenticated users are sent to the login page.
    """

    PUBLIC_PREFIXES = ('/login', '/static', '/media')

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if any(path.startswith(prefix) for prefix in self.PUBLIC_PREFIXES):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return redirect('login')

        if not request.user.is_superuser:
            allowed = {reverse('daily_stock_entry'), reverse('logout')}
            if path not in allowed:
                return redirect('daily_stock_entry')

        return self.get_response(request)
