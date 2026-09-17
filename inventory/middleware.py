from django.conf import settings

from .audit import bind_request, reset_request


class AuditUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip_address = request.META.get("REMOTE_ADDR")
        if settings.FINV_TRUST_PROXY_HEADERS:
            ip_address = request.META.get("HTTP_X_REAL_IP") or ip_address
        token = bind_request(request.user, ip_address)
        try:
            return self.get_response(request)
        finally:
            reset_request(token)
