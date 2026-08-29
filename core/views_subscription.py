import json
import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .apple_iap import apply_apple_transaction, get_verifier as get_apple_verifier, VerificationException
from .google_play import apply_google_subscription
from .models import Subscription

logger = logging.getLogger(__name__)


@require_GET
def subscription_status(request):
    """Polled by the native apps on launch to decide whether to show
    premium features."""
    if not request.user.is_authenticated:
        return JsonResponse({'ok': True, 'authenticated': False, 'has_premium': False})

    subscription, _ = Subscription.objects.get_or_create(user=request.user)
    return JsonResponse({
        'ok': True,
        'authenticated': True,
        'has_premium': subscription.is_active(),
        'platform': subscription.platform,
        'expires_at': subscription.expires_at.isoformat() if subscription.expires_at else None,
    })


@require_GET
def iap_account_token(request):
    """The client must fetch this right before starting the StoreKit /
    Play Billing purchase and pass it along as appAccountToken (iOS) or
    obfuscatedAccountId (Android) — that's what lets the store webhook
    match the purchase back to this account."""
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'authentication required'}, status=401)

    subscription, _ = Subscription.objects.get_or_create(user=request.user)
    return JsonResponse({'ok': True, 'account_token': str(subscription.store_account_token)})


@csrf_exempt
@require_POST
def verify_apple_purchase(request):
    """Called by the app immediately after StoreKit 2 reports a successful
    purchase, so the UI can unlock premium without waiting on the
    server-to-server notification (which can lag by seconds to minutes).
    Body: {"signed_transaction": "<Transaction.jwsRepresentation>"}"""
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'authentication required'}, status=401)

    try:
        body = json.loads(request.body)
        signed_transaction = body['signed_transaction']
    except (ValueError, KeyError):
        return JsonResponse({'ok': False, 'error': 'invalid request body'}, status=400)

    try:
        decoded = get_apple_verifier().verify_and_decode_signed_transaction(signed_transaction)
    except VerificationException:
        logger.warning("Apple purchase verification failed", exc_info=True)
        return JsonResponse({'ok': False, 'error': 'could not verify transaction'}, status=400)

    if decoded.appAccountToken and str(decoded.appAccountToken) != str(
        Subscription.objects.get_or_create(user=request.user)[0].store_account_token
    ):
        return JsonResponse({'ok': False, 'error': 'transaction does not belong to this account'}, status=403)

    subscription = apply_apple_transaction(decoded)
    if subscription is None:
        return JsonResponse({'ok': False, 'error': 'could not link transaction to account'}, status=400)

    return JsonResponse({'ok': True, 'has_premium': subscription.is_active()})


@csrf_exempt
@require_POST
def verify_google_purchase(request):
    """Called by the app immediately after Play Billing reports a
    successful purchase. Body: {"purchase_token": "...", "product_id": "..."}"""
    if not request.user.is_authenticated:
        return JsonResponse({'ok': False, 'error': 'authentication required'}, status=401)

    try:
        body = json.loads(request.body)
        purchase_token = body['purchase_token']
    except (ValueError, KeyError):
        return JsonResponse({'ok': False, 'error': 'invalid request body'}, status=400)

    product_id_hint = body.get('product_id')

    try:
        subscription = apply_google_subscription(purchase_token, product_id_hint)
    except Exception:
        logger.warning("Google purchase verification failed", exc_info=True)
        return JsonResponse({'ok': False, 'error': 'could not verify purchase'}, status=400)

    if subscription is None:
        return JsonResponse({'ok': False, 'error': 'could not link purchase to account'}, status=400)

    if subscription.user_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'purchase does not belong to this account'}, status=403)

    return JsonResponse({'ok': True, 'has_premium': subscription.is_active()})
