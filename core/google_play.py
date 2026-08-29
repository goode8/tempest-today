"""
core/google_play.py

Calls the Google Play Developer API (androidpublisher v3, subscriptionsv2)
to fetch the authoritative state of a subscription purchase. Both the
client-side "I just bought this" check and the Pub/Sub webhook go through
this same call — Google's real-time notifications only carry a purchase
token, not the subscription's actual expiry/state, so a follow-up API call
is required either way.

Setup required: GOOGLE_PLAY_SERVICE_ACCOUNT_PATH, GOOGLE_PLAY_PACKAGE_NAME.
See setup notes for how to create the service account.
"""

from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token, service_account
from googleapiclient.discovery import build

from .models import Subscription

_ACTIVE_STATES = {
    'SUBSCRIPTION_STATE_ACTIVE',
    'SUBSCRIPTION_STATE_IN_GRACE_PERIOD',
}

_androidpublisher = None


def get_client():
    global _androidpublisher
    if _androidpublisher is None:
        credentials = service_account.Credentials.from_service_account_file(
            settings.GOOGLE_PLAY_SERVICE_ACCOUNT_PATH,
            scopes=['https://www.googleapis.com/auth/androidpublisher'],
        )
        _androidpublisher = build('androidpublisher', 'v3', credentials=credentials, cache_discovery=False)
    return _androidpublisher


def fetch_subscription_v2(purchase_token):
    """Raw call to purchases.subscriptionsv2.get. Raises googleapiclient
    HttpError on failure (e.g. invalid/expired token) — callers decide how
    to handle that."""
    return get_client().purchases().subscriptionsv2().get(
        packageName=settings.GOOGLE_PLAY_PACKAGE_NAME,
        token=purchase_token,
    ).execute()


def _parse_rfc3339(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(dt_timezone.utc)


def apply_google_subscription(purchase_token, product_id_hint=None):
    """Fetch current state from Google and update the matching Subscription.
    Returns the Subscription, or None if it can't be linked to a local
    account (no matching store_account_token/purchase_token on file)."""
    data = fetch_subscription_v2(purchase_token)

    external_ids = data.get('externalAccountIdentifiers') or {}
    obfuscated_account_id = external_ids.get('obfuscatedExternalAccountId')

    subscription = None
    if obfuscated_account_id:
        subscription = Subscription.objects.filter(store_account_token=obfuscated_account_id).first()
    if subscription is None:
        subscription = Subscription.objects.filter(purchase_token=purchase_token).first()

    if subscription is None:
        return None

    line_items = data.get('lineItems') or []
    expiry_times = [_parse_rfc3339(item.get('expiryTime')) for item in line_items if item.get('expiryTime')]
    expires_at = max([t for t in expiry_times if t], default=None)
    product_id = (line_items[0].get('productId') if line_items else None) or product_id_hint

    state = data.get('subscriptionState', '')

    subscription.platform = 'android'
    subscription.purchase_token = purchase_token
    if product_id:
        subscription.product_id = product_id
    if expires_at:
        subscription.expires_at = expires_at
    subscription.last_notification_type = state

    if state == 'SUBSCRIPTION_STATE_REVOKED':
        subscription.has_premium = False
        subscription.expires_at = timezone.now()
    else:
        subscription.has_premium = bool(
            state in _ACTIVE_STATES
            and subscription.expires_at
            and subscription.expires_at > timezone.now()
        )

    subscription.save()
    return subscription


def verify_pubsub_bearer_token(auth_header):
    """Verify the OIDC identity token Pub/Sub push attaches to every
    request (as configured on the push subscription), so we know the
    request genuinely came from Google's Pub/Sub service using the
    service account we authorized — not an arbitrary POST to our webhook
    URL. Raises ValueError on any failure."""
    if not auth_header.startswith('Bearer '):
        raise ValueError('missing bearer token')
    token = auth_header[len('Bearer '):]

    claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience=settings.GOOGLE_PUBSUB_AUDIENCE)

    if claims.get('email') != settings.GOOGLE_PUBSUB_SERVICE_ACCOUNT_EMAIL:
        raise ValueError('unexpected service account')
    if not claims.get('email_verified'):
        raise ValueError('service account email not verified')
    return claims


__all__ = ['fetch_subscription_v2', 'apply_google_subscription', 'verify_pubsub_bearer_token']
