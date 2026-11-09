"""Firebase Admin initialization and services."""

from __future__ import annotations

import os

import firebase_admin
from firebase_admin import credentials, firestore, storage

from ..config import Settings, settings


class Firebase:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.app = None
        self.db = None
        self.bucket = None

    def init(self) -> bool:
        """Initialize the Admin SDK. Returns True when ready."""
        if firebase_admin._apps:
            self.app = firebase_admin.get_app()
        else:
            cred = None
            if self.settings.service_account_path and os.path.exists(
                self.settings.service_account_path
            ):
                cred = credentials.Certificate(self.settings.service_account_path)
            elif self.settings.google_app_credentials and os.path.exists(
                self.settings.google_app_credentials
            ):
                cred = credentials.Certificate(self.settings.google_app_credentials)

            if cred is None:
                return False

            self.app = firebase_admin.initialize_app(
                cred,
                {
                    "projectId": self.settings.project_id or None,
                    "storageBucket": self.settings.bucket or None,
                },
            )

        self.db = firestore.client(app=self.app)
        try:
            self.bucket = storage.bucket(name=self.settings.bucket or None)
        except Exception:
            self.bucket = None
        return True

    @property
    def ready(self) -> bool:
        return self.app is not None and self.db is not None


firebase = Firebase(settings)