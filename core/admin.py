from django.contrib import admin
from .models import SearchLog

@admin.register(SearchLog)
class SearchLogAdmin(admin.ModelAdmin):
    list_display  = ('query', 'region', 'count', 'is_random', 'last_searched')
    search_fields = ('query', 'region')
    list_filter   = ('is_random',)
    readonly_fields = ('query', 'region', 'count', 'is_random', 'last_searched')
    