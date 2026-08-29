"""
core/webhooks.py

Server-to-server notification receivers:
  - POST /api/webhooks/apple/  — App Store Server Notifications V2
  - POST /api/webhooks/google/ — Google Play Real-time Developer Notifications (Pub/Sub push)

Both are public endpoints (the store, not a logged-in user, calls these) —
authenticity is established by verifying the payload's signature/token, not
by session auth. Both always return 2xx once the payload has been
successfully parsed and verified, even if it can't be linked to a local
account, so the store doesn't retry indefinitely for something we can't
fix by trying again.
"""

import base64
import json
import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .apple_iap import apply_apple_transaction, get_verifier as get_apple_verifier, VerificationException
from .google_play import apply_google_subscription, verify_pubsub_bearer_token

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def apple_webhook(request):
    try:
        body = json.loads(request.body)
        signed_payload = body['signedPayload']
    except (ValueError, KeyError):
        return HttpResponseBadRequest("malformed payload")

    verifier = get_apple_verifier()
    try:
        notification = verifier.verify_and_decode_notification(signed_payload)
    except VerificationException:
        logger.warning("Apple webhook: signature verification failed", exc_info=True)
        return HttpResponseBadRequest("signature verification failed")

    data = notification.data
    if data is None or not data.signedTransactionInfo:
        # Notification types with no transaction (e.g. TEST) — nothing to update.
        return HttpResponse(status=200)

    try:
        transaction = verifier.verify_and_decode_signed_transaction(data.signedTransactionInfo)
    except VerificationException:
        logger.warning("Apple webhook: transaction verification failed", exc_info=True)
        return HttpResponseBadRequest("transaction verification failed")

    subscription = apply_apple_transaction(transaction, notification_type=notification.notificationType)
    if subscription is None:
        logger.warning(
            "Apple webhook: no local account for originalTransactionId=%s appAccountToken=%s",
            transaction.originalTransactionId, transaction.appAccountToken,
        )

    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def google_webhook(request):
    if settings.GOOGLE_PUBSUB_AUDIENCE:
        try:
            verify_pubsub_bearer_token(request.META.get('HTTP_AUTHORIZATION', ''))
        except ValueError:
            logger.warning("Google webhook: bearer token verification failed", exc_info=True)
            return JsonResponse({'ok': False}, status=401)

    try:
        envelope = json.loads(request.body)
        message_data = envelope['message']['data']
    except (ValueError, KeyError):
        return HttpResponseBadRequest("malformed envelope")

    try:
        payload = json.loads(base64.b64decode(message_data))
    except (ValueError, TypeError):
        return HttpResponseBadRequest("malformed message data")

    notification = payload.get('subscriptionNotification')
    if not notification:
        # testNotification, oneTimeProductNotification, etc — nothing to do.
        return HttpResponse(status=200)

    purchase_token = notification.get('purchaseToken')
    product_id = notification.get('subscriptionId')
    if not purchase_token:
        return HttpResponse(status=200)

    try:
        subscription = apply_google_subscription(purchase_token, product_id)
    except Exception:
        logger.warning("Google webhook: failed to fetch subscription state", exc_info=True)
        # Ask Google to retry — this failure could be transient (API outage).
        return JsonResponse({'ok': False}, status=500)

    if subscription is None:
        logger.warning("Google webhook: no local account for purchaseToken=%s", purchase_token)

    return HttpResponse(status=200)
