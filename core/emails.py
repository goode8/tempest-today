from django.conf import settings
from django.core.mail import send_mail


def send_magic_link_email(email, token, next_url=''):
    verify_url = f"{settings.SITE_BASE_URL}/api/auth/magic-link/verify/?token={token}"
    if next_url:
        verify_url += f"&next={next_url}"

    send_mail(
        subject="Your Tempest Today sign-in link",
        message=(
            "Tap the link below to sign in. It expires in 15 minutes and "
            "can only be used once.\n\n"
            f"{verify_url}\n\n"
            "If you didn't request this, you can ignore this email."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
