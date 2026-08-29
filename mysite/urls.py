import os
from django.http import HttpResponse
from django.urls import include, path
from core.views import index, compact_forecast, register_push, update_push_cities
from core.views_auth import magic_link_request, magic_link_verify
from core.views_subscription import (
    subscription_status, iap_account_token, verify_apple_purchase, verify_google_purchase,
)
from core.webhooks import apple_webhook, google_webhook
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

    # Accounts — created on demand only, when a free user hits a premium gate.
    path("accounts/", include("allauth.urls")),  # Google/Apple OAuth redirect + callback
    path("api/auth/magic-link/request/", magic_link_request, name="magic_link_request"),
    path("api/auth/magic-link/verify/", magic_link_verify, name="magic_link_verify"),

    # Subscription status + purchase linking
    path("api/subscription-status/", subscription_status, name="subscription_status"),
    path("api/iap/account-token/", iap_account_token, name="iap_account_token"),
    path("api/iap/verify-apple-purchase/", verify_apple_purchase, name="verify_apple_purchase"),
    path("api/iap/verify-google-purchase/", verify_google_purchase, name="verify_google_purchase"),

    # Store server-to-server notifications
    path("api/webhooks/apple/", apple_webhook, name="apple_webhook"),
    path("api/webhooks/google/", google_webhook, name="google_webhook"),

    path(f"{SECRET_ADMIN_URL_PATH}/", admin.site.urls),
]