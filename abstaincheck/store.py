"""Storage for tests and results. Firestore in production, memory (optionally mirrored to a
JSON file) for local runs and tests. Nothing here ever holds an API key."""
import json
import os
import pathlib
import threading


class MemoryStore:
    def __init__(self, path=None):
        self.path = pathlib.Path(path) if path else None
        self.lock = threading.Lock()
        self.data = {"tests": {}, "results": {}}
        if self.path and self.path.exists():
            self.data = json.loads(self.path.read_text())

    def _flush(self):
        if self.path:
            self.path.write_text(json.dumps(self.data))

    def put(self, kind, key, doc):
        with self.lock:
            self.data[kind][key] = doc
            self._flush()

    def get(self, kind, key):
        return self.data[kind].get(key)

    def update(self, kind, key, fields):
        with self.lock:
            self.data[kind][key].update(fields)
            self._flush()

    def delete(self, kind, key):
        with self.lock:
            self.data[kind].pop(key, None)
            self._flush()

    def published(self):
        return [r for r in self.data["results"].values() if r.get("published")]


class FirestoreStore:
    def __init__(self, project=None):
        from google.cloud import firestore
        self.db = firestore.Client(project=project)

    def put(self, kind, key, doc):
        self.db.collection(kind).document(key).set(doc)

    def get(self, kind, key):
        snap = self.db.collection(kind).document(key).get()
        return snap.to_dict() if snap.exists else None

    def update(self, kind, key, fields):
        self.db.collection(kind).document(key).update(fields)

    def delete(self, kind, key):
        self.db.collection(kind).document(key).delete()

    def published(self):
        q = self.db.collection("results").where("published", "==", True)
        return [d.to_dict() for d in q.stream()]


def make_store():
    if os.environ.get("ABSTAINCHECK_STORE") == "firestore":
        return FirestoreStore(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    return MemoryStore(os.environ.get("ABSTAINCHECK_LOCAL_STORE"))
