from django.contrib import admin
from .models import SearchLog, DeviceToken, Subscription, MagicLinkToken

@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display  = ('query', 'region', 'count', 'is_random', 'last_searched')
    search_fields = ('query', 'region')
    list_filter   = ('is_random',)
    readonly_fields = ('query', 'region', 'count', 'is_random', 'last_searched')


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display  = ('__str__', 'user', 'platform', 'city_1', 'city_2', 'city_3', 'updated_at')
    list_filter   = ('platform',)
    search_fields = ('token', 'city_1', 'city_2', 'city_3', 'user__email')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display  = ('user', 'has_premium', 'platform', 'expires_at', 'updated_at')
    list_filter   = ('platform', 'has_premium')
    search_fields = ('user__email', 'original_transaction_id', 'purchase_token')
    readonly_fields = ('store_account_token', 'updated_at')


@admin.register(MagicLinkToken)
class MagicLinkTokenAdmin(admin.ModelAdmin):
    list_display  = ('email', 'created_at', 'used_at')
    search_fields = ('email',)
    readonly_fields = ('token', 'created_at')
