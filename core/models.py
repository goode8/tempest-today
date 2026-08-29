import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class DeviceToken(models.Model):
    PLATFORM_CHOICES = [('android', 'Android'), ('ios', 'iOS')]

    # Nullable: rows registered before premium-gating existed have no owner.
    # They're orphaned going forward (skipped by check_and_send_alerts) until
    # that install logs in and re-registers under an account.
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.CASCADE, related_name='device_tokens',
    )
    token    = models.CharField(max_length=512, unique=True)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES)
    city_1   = models.CharField(max_length=200, blank=True)
    city_2   = models.CharField(max_length=200, blank=True)
    city_3   = models.CharField(max_length=200, blank=True)
    last_alert_state = models.TextField(default='{}')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def cities(self):
        return [c for c in [self.city_1, self.city_2, self.city_3] if c]

    def __str__(self):
        cities = ', '.join(self.cities()) or 'no cities'
        return f"{self.platform}: {self.token[:20]}… ({cities})"


class SearchLog(models.Model):
    query       = models.CharField(max_length=200, unique=True)  # what they typed
    region      = models.CharField(max_length=200, blank=True, null=True)  # resolved city/state
    count       = models.PositiveIntegerField(default=1)
    last_searched = models.DateTimeField(auto_now=True)
    is_random   = models.BooleanField(default=False)  # whether it was a random search
    class Meta:
        ordering = ['-count']

    def __str__(self):
        return f"{'🎲' if self.is_random else '🔍'} {self.query} → {self.region} ({self.count}x)"


class Subscription(models.Model):
    """One row per user, tracking their premium entitlement.

    `store_account_token` is generated once per user and must be passed by
    the client as Apple's `appAccountToken` / Google's `obfuscatedAccountId`
    when starting the IAP purchase. That's what lets the Apple/Google
    webhooks (which only ever see store-side identifiers) find the right
    user without any prior handshake.
    """
    PLATFORM_CHOICES = [('ios', 'iOS'), ('android', 'Android'), ('none', 'None')]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='subscription',
    )
    store_account_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    has_premium = models.BooleanField(default=False)
    platform = models.CharField(max_length=10, choices=PLATFORM_CHOICES, default='none')
    product_id = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    # Reconciliation keys. Nullable+unique so sqlite/Postgres both allow
    # multiple NULLs (i.e. before a user has ever purchased anything).
    original_transaction_id = models.CharField(max_length=200, blank=True, null=True, unique=True)  # Apple
    purchase_token = models.CharField(max_length=1000, blank=True, null=True, unique=True)  # Google

    last_notification_type = models.CharField(max_length=50, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def is_active(self):
        return bool(self.has_premium and self.expires_at and self.expires_at > timezone.now())

    def __str__(self):
        return f"{self.user}: {'premium' if self.is_active() else 'free'}"


def _generate_magic_link_token():
    return secrets.token_urlsafe(32)


class MagicLinkToken(models.Model):
    """Single-use, short-lived passwordless login token emailed to the user."""
    TTL_MINUTES = 15

    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True, default=_generate_magic_link_token)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def is_valid(self):
        if self.used_at is not None:
            return False
        return self.created_at > timezone.now() - timedelta(minutes=self.TTL_MINUTES)

    def __str__(self):
        return f"magic link for {self.email} ({'used' if self.used_at else 'unused'})"