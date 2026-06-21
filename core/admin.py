from django.contrib import admin
from .models import SearchLog, DeviceToken

@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display  = ('query', 'region', 'count', 'is_random', 'last_searched')
    search_fields = ('query', 'region')
    list_filter   = ('is_random',)
    readonly_fields = ('query', 'region', 'count', 'is_random', 'last_searched')


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display  = ('__str__', 'platform', 'city_1', 'city_2', 'city_3', 'updated_at')
    list_filter   = ('platform',)
    search_fields = ('token', 'city_1', 'city_2', 'city_3')
    readonly_fields = ('created_at', 'updated_at')
    