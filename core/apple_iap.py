"""
core/apple_iap.py

Verifies Apple App Store Server Notifications V2 and StoreKit 2 transaction
JWS strings using Apple's own `app-store-server-library` (import name:
appstoreserverlibrary) — this handles the x5c certificate-chain verification
against Apple's root CA and the JWS signature check. We never hand-roll
crypto here.

Setup required (see project README / setup notes):
1. Download Apple's public root CA certificates and place the .cer files in
   the directory pointed to by APPLE_ROOT_CERTS_DIR.
2. Set APPLE_BUNDLE_ID, APPLE_APP_APPLE_ID, APPLE_IAP_ENVIRONMENT.
"""

import os
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from appstoreserverlibrary.signed_data_verifier import SignedDataVerifier, VerificationException
from appstoreserverlibrary.models.Environment import Environment as AppleEnvironment
from appstoreserverlibrary.models.NotificationTypeV2 import NotificationTypeV2

from .models import Subscription

_verifier = None


def _load_root_certificates():
    certs_dir = settings.APPLE_ROOT_CERTS_DIR
    if not os.path.isdir(certs_dir):
        raise RuntimeError(
            f"APPLE_ROOT_CERTS_DIR ({certs_dir}) does not exist or is not a "
            "directory. Download Apple's root CA certificates first — see "
            "setup notes."
        )
    certs = []
    for name in sorted(os.listdir(certs_dir)):
        if name.lower().endswith(('.cer', '.der', '.pem')):
            with open(os.path.join(certs_dir, name), 'rb') as f:
                certs.append(f.read())
    if not certs:
        raise RuntimeError(f"No root certificates found in {certs_dir}.")
    return certs


def get_verifier():
    global _verifier
    if _verifier is None:
        environment = (
            AppleEnvironment.SANDBOX
            if settings.APPLE_IAP_ENVIRONMENT.lower() == 'sandbox'
            else AppleEnvironment.PRODUCTION
        )
        _verifier = SignedDataVerifier(
            root_certificates=_load_root_certificates(),
            enable_online_checks=True,
            environment=environment,
            bundle_id=settings.APPLE_BUNDLE_ID,
            app_apple_id=settings.APPLE_APP_APPLE_ID,
        )
    return _verifier


def _ms_to_datetime(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=dt_timezone.utc)


def apply_apple_transaction(decoded_transaction, notification_type=None):
    """Find (or fail to find) the Subscription this transaction belongs to
    and update it. Returns the Subscription, or None if no local account
    is linked to this purchase yet (e.g. a renewal notification arriving
    for a purchase whose original appAccountToken never matched a user —
    logged as a warning, not an error, since Apple will keep retrying and
    there's nothing actionable to do server-side)."""
    subscription = None
    if decoded_transaction.appAccountToken:
        subscription = Subscription.objects.filter(
            store_account_token=decoded_transaction.appAccountToken
        ).first()
    if subscription is None and decoded_transaction.originalTransactionId:
        subscription = Subscription.objects.filter(
            original_transaction_id=decoded_transaction.originalTransactionId
        ).first()

    if subscription is None:
        return None

    subscription.platform = 'ios'
    subscription.original_transaction_id = decoded_transaction.originalTransactionId
    if decoded_transaction.productId:
        subscription.product_id = decoded_transaction.productId
    if notification_type:
        subscription.last_notification_type = str(notification_type)

    revoked = (
        decoded_transaction.revocationReason is not None
        or notification_type in (NotificationTypeV2.REFUND, NotificationTypeV2.REVOKE)
    )
    if revoked:
        subscription.has_premium = False
        subscription.expires_at = timezone.now()
    else:
        expires_at = _ms_to_datetime(decoded_transaction.expiresDate)
        if expires_at:
            subscription.expires_at = expires_at
        subscription.has_premium = bool(
            subscription.expires_at and subscription.expires_at > timezone.now()
        )

    subscription.save()
    return subscription


__all__ = ['get_verifier', 'apply_apple_transaction', 'VerificationException']
