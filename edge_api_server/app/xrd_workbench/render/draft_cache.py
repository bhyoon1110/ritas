"""Bounded, private, process-local preview image cache. Never used for final exports."""
from collections import OrderedDict
import hashlib
import json
import threading
import time


class DraftImageCache:
    def __init__(self, max_bytes=32 * 1024 * 1024, max_entries=48, ttl=600):
        self.max_bytes, self.max_entries, self.ttl = max_bytes, max_entries, ttl
        self.entries, self.size, self.lock = OrderedDict(), 0, threading.Lock()

    def render(self, scope, renderer, *args):
        key = (str(scope), hashlib.sha256(json.dumps(args, sort_keys=True,
               ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()).hexdigest())
        with self.lock:
            now = time.monotonic()
            for old in list(self.entries):
                if now - self.entries[old][0] > self.ttl:
                    self.size -= len(self.entries.pop(old)[1])
            if key in self.entries:
                self.entries.move_to_end(key)
                return self.entries[key][1]
        result = renderer(*args)
        if len(result) <= self.max_bytes:
            with self.lock:
                if key in self.entries:
                    self.size -= len(self.entries.pop(key)[1])
                self.entries[key] = (time.monotonic(), result)
                self.size += len(result)
                while self.size > self.max_bytes or len(self.entries) > self.max_entries:
                    self.size -= len(self.entries.popitem(last=False)[1][1])
        return result


draft_images = DraftImageCache()
