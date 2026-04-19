class ClientPlatformMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        ua = request.META.get("HTTP_USER_AGENT", "")
        if "TempestTodayApp/android" in ua:
            request.client_platform = "android"
        else:
            request.client_platform = "web"
        return self.get_response(request)
