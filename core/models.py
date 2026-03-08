from django.db import models

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