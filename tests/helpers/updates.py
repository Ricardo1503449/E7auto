"""Synthetic signed feed transport; never uses the production private key."""
import hashlib
from e7auto.core.update_feed import PROJECT_URL, REPOSITORY, encode_feed
from PySide6.QtCore import QIODevice
from PySide6.QtNetwork import QNetworkReply


def payload(version='v9.0.0', *, notes='## Changes', sha256='a' * 64, size=123):
    name = f'E7auto_{version}_Setup_x64.exe'
    return {'repository': REPOSITORY, 'version': version, 'published_at': '2026-11-05T00:00:00Z',
            'commit': 'a' * 40, 'notes': notes,
            'asset': {'name': name, 'url': f'{PROJECT_URL}/releases/download/{version}/{name}',
                      'size': size, 'sha256': sha256}}


def feed(version='v9.0.0', **kwargs):
    return encode_feed(payload(version, **kwargs), lambda data: hashlib.sha256(data).digest())


def verify_test_signature(data, signature):
    if hashlib.sha256(data).digest() != signature:
        raise ValueError('synthetic signature mismatch')


class FeedReply(QNetworkReply):
    def __init__(self, payload=b'', status=200, headers=None):
        super().__init__()
        self.payload, self.status, self.offset = payload, status, 0
        self.aborted = False
        for name, value in (headers or {}).items():
            self.setRawHeader(name, value)
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def attribute(self, _attribute):
        return self.status

    def error(self):
        return QNetworkReply.NetworkError.OperationCanceledError if self.aborted or self.status != 200 else QNetworkReply.NetworkError.NoError

    def bytesAvailable(self):
        return len(self.payload) - self.offset + super().bytesAvailable()

    def readData(self, size):
        block = self.payload[self.offset:self.offset + size]
        self.offset += len(block)
        return block

    def complete(self):
        self.setFinished(True)
        self.readyRead.emit()
        if not self.aborted:
            self.finished.emit()

    def abort(self):
        self.aborted = True
        self.setFinished(True)
        self.finished.emit()
