import asyncio
import redis.asyncio as aioredis
import json
import time
from typing import Optional, Dict, Any, List
from datetime import datetime


class AsyncRedisDict:    
    def __init__(self, redis_url: str = "redis://localhost:6379", 
                 initial_data: Optional[Dict[str, Any]] = None,
                 default_ttl: Optional[int] = None):
        """
        初始化 Redis 字典
        
        Args:
            redis_url: Redis 连接地址
            initial_data: 初始键值对，格式: {"key": value, ...}
            default_ttl: 初始数据的默认过期时间（秒），None表示永不过期
        """
        self.redis_url = redis_url
        self.key_prefix = ""
        self.redis = None
        self._connected = False
        self.initial_data = initial_data or {}
        self.default_ttl = default_ttl
    
    async def connect(self):
        """建立 Redis 连接"""
        if not self._connected:
            self.redis = await aioredis.from_url(
                self.redis_url,
                max_connections=20,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True
            )
            self._connected = True
            print(f"Redis 连接成功: {self.redis_url}")
            
            # 如果有初始数据，自动加载
            if self.initial_data:
                await self.set_many(self.initial_data, default_ttl=self.default_ttl)
                print(f"已加载 {len(self.initial_data)} 个初始键值对")
    
    async def close(self):
        """关闭 Redis 连接"""
        if self._connected and self.redis:
            await self.redis.close()
            await self.redis.connection_pool.disconnect()
            self._connected = False
            print("Redis 连接已关闭")
    
    def _make_key(self, key: str) -> str:
        """生成完整的 Redis 键名"""
        return f"{self.key_prefix}{key}"
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = 600):
        """
        设置键值对，创建时指定过期时间
        
        Args:
            key: 键名
            value: 值（会自动序列化为 JSON）
            ttl: 过期时间（秒），None 表示永不过期
        """
        full_key = self._make_key(key)
        json_value = json.dumps(value, ensure_ascii=False)
        if ttl == None:
            ttl = self.default_ttl
        
        await self.redis.setex(full_key, ttl, json_value)
    
    async def get(self, key: str, default: Any = None) -> Any:
        full_key = self._make_key(key)
        value = await self.redis.get(full_key)
        
        if value is None:
            return default
        
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    
    async def get_ttl(self, key: str) -> int:
        """
        获取键的剩余过期时间
        """
        full_key = self._make_key(key)
        return await self.redis.ttl(full_key)
    
    async def delete(self, key: str) -> bool:
        full_key = self._make_key(key)
        deleted = await self.redis.delete(full_key)
        return deleted > 0
    
    async def exists(self, key: str) -> bool:
        """
        检查键是否存在
        """
        full_key = self._make_key(key)
        return await self.redis.exists(full_key) > 0
    
    async def clear_all(self) -> int:
        """
        清空所有键（慎用）
        """
        pattern = f"{self.key_prefix}*"
        cursor = 0
        deleted = 0
        
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=1000)
            if keys:
                deleted += await self.redis.delete(*keys)
            if cursor == 0:
                break
        
        print(f"[{datetime.now().strftime('%H:%M:%S')}] 清空了 {deleted} 个键")
        return deleted