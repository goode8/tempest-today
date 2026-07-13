"""
core/firebase.py

Shared Firebase Admin SDK initialization.
Import `messaging` from this module anywhere you need to send a push.

Setup required:
1. Get your service account JSON from:
   Firebase console -> Project Settings -> Service accounts -> Generate new private key
2. Save it somewhere NOT in git (e.g. project root, add to .gitignore).
3. Set the path via an environment variable (recommended) or hardcode below for testing.
"""

import os
import firebase_admin
from firebase_admin import credentials, messaging

_CRED_PATH = os.environ.get(
    "FIREBASE_CREDENTIALS_PATH",
    "/absolute/path/to/serviceAccountKey.json",  # <-- update this for local testing
)

if not firebase_admin._apps:
    cred = credentials.Certificate(_CRED_PATH)
    firebase_admin.initialize_app(cred)

# re-export so other files can do: from core.firebase import messaging
__all__ = ["messaging"]
