import json
import re

from django.contrib.auth import get_user_model, login
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .emails import send_magic_link_email
from .models import MagicLinkToken

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@csrf_exempt
@require_POST
def magic_link_request(request):
    """Start passwordless login: email a single-use link. Always returns a
    generic success message regardless of whether the address has an
    account yet — the account itself is only created once the link is
    clicked, so we don't leak which emails exist."""
    try:
        body = json.loads(request.body)
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'invalid json'}, status=400)

    email = (body.get('email') or '').strip().lower()
    next_url = (body.get('next') or '').strip()

    if not email or not EMAIL_RE.match(email):
        return JsonResponse({'ok': False, 'error': 'invalid email'}, status=400)

    # Basic per-email rate limit: 1 request per 60s.
    rate_key = f"magic-link-rl-{email}"
    if cache.get(rate_key):
        return JsonResponse({'ok': True})  # pretend success, don't reveal the limit
    cache.set(rate_key, True, 60)

    token = MagicLinkToken.objects.create(email=email)
    send_magic_link_email(email, token.token, next_url)

    return JsonResponse({'ok': True})


@require_GET
def magic_link_verify(request):
    """Land here from the emailed link. Verifies the token, creates the
    account on first use, logs the session in, then redirects to `next`
    (typically back into the paywall flow the user started from)."""
    token_value = request.GET.get('token', '')
    next_url = request.GET.get('next') or '/'

    try:
        token = MagicLinkToken.objects.get(token=token_value)
    except MagicLinkToken.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'invalid or expired link'}, status=400)

    if not token.is_valid():
        return JsonResponse({'ok': False, 'error': 'invalid or expired link'}, status=400)

    token.used_at = timezone.now()
    token.save(update_fields=['used_at'])

    User = get_user_model()
    user, _ = User.objects.get_or_create(
        email=token.email,
        defaults={'username': token.email},
    )

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')

    return redirect(next_url)
