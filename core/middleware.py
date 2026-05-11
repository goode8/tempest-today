from django.http import HttpResponseForbidden

BLOCKED_IPS = {
    '3.64.223.136',
}


class BlockedIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        ip = ip.split(',')[0].strip()
        if ip in BLOCKED_IPS:
            return HttpResponseForbidden()
        return self.get_response(request)


class ClientPlatformMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ua = request.META.get("HTTP_USER_AGENT", "")
        if "TempestTodayApp/android" in ua:
            request.client_platform = "android"
        elif "TempestTodayApp/ios" in ua:
            request.client_platform = "ios"
        else:
            request.client_platform = "web"
        return self.get_response(request)
