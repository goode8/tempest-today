import os
from django.http import HttpResponse
from django.urls import path
from core.views import index, compact_forecast, register_push, update_push_cities
from django.contrib import admin

SECRET_ADMIN_URL_PATH = os.environ.get('SECRET_ADMIN_URL_PATH', 'default-fallback')

def robots_txt(request):
    lines = [
        "# Allow major search engines",
        "User-agent: Googlebot",
        "User-agent: Bingbot",
        "User-agent: Slurp",
        "User-agent: DuckDuckBot",
        "Allow: /",
        "",
        "# Allow social media crawlers",
        "User-agent: facebookexternalhit",
        "User-agent: Twitterbot",
        "User-agent: LinkedInBot",
        "Allow: /",
        "",
        "# Allow OpenAI SearchBot",
        "User-agent: OAI-SearchBot",
        "Allow: /",
        "",
        "# Block SEO crawlers",
        "User-agent: SemrushBot",
        "User-agent: AhrefsBot",
        "User-agent: MJ12bot",
        "User-agent: DotBot",
        "Disallow: /",
        "",
        "# Block everything else",
        "User-agent: *",
        "Disallow: /",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")

urlpatterns = [
    path("", index, name="index"),
    path("compact/", compact_forecast, name="compact_forecast"),
    path("register-push/", register_push, name="register_push"),
    path("update-push-cities/", update_push_cities, name="update_push_cities"),
    path("robots.txt", robots_txt),
    path(f"{SECRET_ADMIN_URL_PATH}/", admin.site.urls),
]