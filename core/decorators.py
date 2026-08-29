from functools import wraps

from django.http import JsonResponse


def premium_required(view_func):
    """Reject the request unless the session belongs to a logged-in user
    with an active subscription. Applies regardless of what the client UI
    showed — this is the real gate, not the toggle/button visibility."""
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'ok': False, 'error': 'authentication required'}, status=401)
        subscription = getattr(request.user, 'subscription', None)
        if not subscription or not subscription.is_active():
            return JsonResponse({'ok': False, 'error': 'premium subscription required'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapped
