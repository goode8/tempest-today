import ipaddress
from django.http import HttpResponseForbidden

BLOCKED_IPS = {
    '3.64.223.136',
}

# fake-iPhone botnet 
BLOCKED_NETWORKS = [
    ipaddress.ip_network('43.128.0.0/10'),
    ipaddress.ip_network('49.51.0.0/16'),
    ipaddress.ip_network('129.226.0.0/16'),
]


class BlockedIPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ip_str = request.META.get('HTTP_X_FORWARDED_FOR', request.META.get('REMOTE_ADDR', ''))
        ip_str = ip_str.split(',')[0].strip()

        if ip_str in BLOCKED_IPS:
            return HttpResponseForbidden()

        try:
            ip = ipaddress.ip_address(ip_str)
            if any(ip in net for net in BLOCKED_NETWORKS):
                return HttpResponseForbidden()
        except ValueError:
            pass

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

        # Render the "use my location" pin for both native platforms, but on iOS
        # it starts hidden and JS only reveals it when the native location bridge
        # (window.TempestNative) is present — i.e. the working TestFlight build.
        # The published App Store build has no bridge, so testers see a working
        # button while regular users never see a dead one.
        request.show_locate = request.client_platform in ("android", "ios")
        return self.get_response(request)
