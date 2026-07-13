"""
core/management/commands/send_test_push.py

Usage:
    python manage.py send_test_push --platform ios
    python manage.py send_test_push --platform android
    python manage.py send_test_push --token <specific_token_value>
"""

from django.core.management.base import BaseCommand, CommandError
from core.models import DeviceToken
from core.firebase import messaging


class Command(BaseCommand):
    help = "Send a test push notification to one device token."

    def add_arguments(self, parser):
        parser.add_argument(
            "--platform",
            choices=["ios", "android"],
            help="Send to the most recently updated token for this platform.",
        )
        parser.add_argument(
            "--token",
            help="Send to this exact device token instead of looking one up.",
        )
        parser.add_argument(
            "--title",
            default="Test Weather Alert",
        )
        parser.add_argument(
            "--body",
            default="This is a test push notification.",
        )

    def handle(self, *args, **options):
        token_value = options.get("token")

        if not token_value:
            platform = options.get("platform")
            if not platform:
                raise CommandError("Pass either --platform ios/android or --token <value>.")

            # NOTE: adjust 'iOS'/'Android' to match your actual Platform values
            # (your screenshot shows 'iOS' and 'Android' — confirm casing matches your model choices)
            platform_value = "ios" if platform == "ios" else "android"
            device = (
                DeviceToken.objects.filter(platform=platform_value)
                .order_by("-updated_at")
                .first()
            )
            if not device:
                raise CommandError(f"No device token found for platform={platform_value}")
            token_value = device.token

        message = messaging.Message(
            notification=messaging.Notification(
                title=options["title"],
                body=options["body"],
            ),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default")
                )
            ),
            token=token_value,
        )

        try:
            response = messaging.send(message)
        except Exception as e:
            raise CommandError(f"Send failed: {e}")

        self.stdout.write(self.style.SUCCESS(f"Sent. Message ID: {response}"))
