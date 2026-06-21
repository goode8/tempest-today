from django.db import models


class DeviceToken(models.Model):
    PLATFORM_CHOICES = [('android', 'Android'), ('ios', 'iOS')]

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