# src/ttl_dict.py
import asyncio
import json
import time
from typing import Any, Optional
from collections import defaultdict

class AsyncTTLItem:
    def __init__(self, value: Any, ttl: Optional[int]):
        self.value = value
        self.expires_at = time.time() + (ttl if ttl is not None else float('inf'))

class AsyncTTLDict:
    def __init__(self, default_ttl: Optional[int] = 600, cleanup_interval: int = 60):
        self._data: dict[str, AsyncTTLItem] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        self._cleanup_task = None

    async def start_cleanup(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._periodic_cleanup())

    async def _periodic_cleanup(self):
        while True:
            await asyncio.sleep(120)  # 每30秒检查一次
            async with self._lock:
                now = time.time()
                to_delete = [k for k, v in self._data.items() if v.expires_at <= now]
                for k in to_delete:
                    del self._data[k]

    async def set(self, key: str, value: Any, ttl: Optional[int] = None):
        if ttl is None:
            ttl = self._default_ttl
        json_value = json.dumps(value, ensure_ascii=False)
        async with self._lock:
            self._data[key] = AsyncTTLItem(json_value, ttl)

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._lock:
            item = self._data.get(key)
            if item is None:
                return default
            if time.time() > item.expires_at:
                del self._data[key]
                return default
            try:
                return json.loads(item.value)
            except json.JSONDecodeError:
                return item.value

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return self._data.pop(key, None) is not None

    async def exists(self, key: str) -> bool:
        async with self._lock:
            item = self._data.get(key)
            if item and time.time() <= item.expires_at:
                return True
            if item:
                del self._data[key]
            return False