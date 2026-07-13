"""
Management command: check_and_send_alerts

Run every 10 minutes via PythonAnywhere scheduled task:
  /path/to/venv/bin/python manage.py check_and_send_alerts

Checks active weather alerts for each registered device's saved cities.
Sends an FCM push notification when the alert state changes (new alerts
appear or existing ones clear). Skips cities that fail to geocode.

Requires:
  - pip install firebase-admin
  - FIREBASE_SERVICE_ACCOUNT_PATH env var pointing to the service account JSON
"""

import json
import hashlib
import os

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import DeviceToken
from core.utils import geocode_query
from core.weather_service import WeatherService

# Lazily initialised Firebase app (survives multiple Command instances in the
# same process, though each scheduled invocation is a fresh process).
_firebase_app = None


def _get_firebase_app():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    import firebase_admin
    from firebase_admin import credentials

    sa_path = os.environ.get('FIREBASE_SERVICE_ACCOUNT_PATH', '')
    if not sa_path or not os.path.exists(sa_path):
        raise RuntimeError(
            'FIREBASE_SERVICE_ACCOUNT_PATH is not set or the file does not exist. '
            'Download your Firebase service account JSON and set the env var.'
        )

    cred = credentials.Certificate(sa_path)
    _firebase_app = firebase_admin.initialize_app(cred)
    return _firebase_app


def _fetch_alerts_for_city(q, ws):
    """
    Geocode q, fetch active alerts, return list of alert dicts.
    Returns empty list on any failure.
    """
    try:
        result = geocode_query(q)
        if result is None:
            return []
        lat, lon, is_canada = result

        if is_canada:
            _, _, alerts = ws.get_eccc_weather(lat, lon)
        else:
            alerts = ws.get_active_alerts(lat, lon)

        return alerts or []
    except Exception as e:
        print(f'[alerts] error fetching alerts for {q!r}: {e}')
        return []


def _build_state(city_alerts):
    """
    Build a compact, stable representation of the current alert state.
    city_alerts: {city_query: [alert_dict, ...]}
    Returns a JSON string sorted for stable hashing.
    """
    compact = {}
    for city, alerts in city_alerts.items():
        compact[city] = sorted(
            a.get('event', '') for a in alerts if a.get('event')
        )
    return json.dumps(compact, sort_keys=True)


def _pick_notification(city_alerts):
    """
    Choose the best title/body for the push notification.
    Returns (title, body, data_dict).
    """
    severity_rank = {'Extreme': 0, 'Severe': 1, 'Moderate': 2, 'Minor': 3}
    best_city = None
    best_alert = None
    best_rank = 999

    for city, alerts in city_alerts.items():
        for alert in alerts:
            rank = severity_rank.get(alert.get('severity', ''), 4)
            if rank < best_rank:
                best_rank = rank
                best_city = city
                best_alert = alert

    if not best_alert:
        return None, None, {}

    event = best_alert.get('event') or 'Weather Alert'
    title = f'⚠️ {event}'
    body  = best_alert.get('headline') or event
    data  = {'city': best_city, 'event': event}
    return title, body, data


class Command(BaseCommand):
    help = 'Check weather alerts for all registered devices and send FCM push notifications.'

    def handle(self, *args, **options):
        from firebase_admin import messaging

        try:
            _get_firebase_app()
        except RuntimeError as e:
            self.stderr.write(str(e))
            return

        ws = WeatherService()
        devices = DeviceToken.objects.all()
        total = devices.count()

        if total == 0:
            self.stdout.write('No registered devices.')
            return

        self.stdout.write(f'Checking alerts for {total} device(s)…')
        sent = 0
        errors = 0

        for device in devices:
            cities = device.cities()
            if not cities:
                continue

            # Fetch alerts for all saved cities
            city_alerts = {}
            for city in cities:
                alerts = _fetch_alerts_for_city(city, ws)
                if alerts:
                    city_alerts[city] = alerts

            new_state = _build_state(city_alerts)
            new_hash  = hashlib.sha256(new_state.encode()).hexdigest()

            # Load previous state hash
            try:
                prev = json.loads(device.last_alert_state or '{}')
                prev_hash = prev.get('hash', '')
            except (ValueError, TypeError):
                prev_hash = ''

            # Only notify if state changed AND there are active alerts
            if new_hash == prev_hash or not city_alerts:
                device.last_alert_state = json.dumps({'hash': new_hash})
                device.save(update_fields=['last_alert_state'])
                continue

            title, body, data = _pick_notification(city_alerts)
            if not title:
                continue

            notification = messaging.Notification(title=title, body=body)
            fcm_data = {k: str(v) for k, v in data.items()}

            if device.platform == 'ios':
                msg = messaging.Message(
                    notification=notification,
                    data=fcm_data,
                    token=device.token,
                    apns=messaging.APNSConfig(
                        payload=messaging.APNSPayload(
                            aps=messaging.Aps(
                                alert=messaging.ApsAlert(title=title, body=body),
                                sound='default',
                                content_available=True,
                            )
                        )
                    ),
                )
            else:
                msg = messaging.Message(
                    notification=notification,
                    data=fcm_data,
                    token=device.token,
                )

            try:
                messaging.send(msg)
                sent += 1
                self.stdout.write(f'  sent to {device.platform} …{device.token[-8:]}: {title}')
            except messaging.UnregisteredError:
                # Token is stale — remove it so we stop hitting it
                self.stdout.write(f'  removing stale token …{device.token[-8:]}')
                device.delete()
                continue
            except Exception as e:
                self.stderr.write(f'  FCM error for …{device.token[-8:]}: {e}')
                errors += 1
                continue

            device.last_alert_state = json.dumps({'hash': new_hash})
            device.save(update_fields=['last_alert_state', 'updated_at'])

        self.stdout.write(f'Done. Sent: {sent}, Errors: {errors}')
