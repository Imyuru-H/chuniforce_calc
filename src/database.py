# src/database.py
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
import json
import logging
from datetime import datetime, timedelta

from .user_data import Base, UserData

logger = logging.getLogger(__name__)

# 使用 aiosqlite 支持异步操作
DATABASE_URL = "sqlite+aiosqlite:///./user_data.db"

# 创建异步引擎
engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # 设为 True 可查看 SQL 日志
    future=True,
)

# 创建异步 session 工厂
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

class DatabaseManager:
    """数据库管理器 - 基于UUID存储"""
    
    @staticmethod
    async def init_db():
        """初始化数据库，创建表"""
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database initialized successfully")
    
    @staticmethod
    @asynccontextmanager
    async def get_session():
        """获取数据库会话"""
        async with AsyncSessionLocal() as session:
            try:
                yield session
            except Exception as e:
                await session.rollback()
                logger.error(f"Database error: {e}")
                raise
            finally:
                await session.close()
    
    @staticmethod
    async def save_user_data(
        uuid: str,
        player_data: Dict,
        b50_list: List,
        ajc_list: List,
        ajc_count: int,
        force_result: float = 0.0,
    ) -> bool:
        """
        保存或更新用户数据（基于UUID）
        如果UUID已存在则更新，否则创建新记录
        """
        try:
            async with DatabaseManager.get_session() as session:
                # 检查UUID是否已存在
                from sqlalchemy import select
                stmt = select(UserData).where(UserData.uuid == uuid)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                
                if existing:
                    # 更新现有记录
                    existing.player_data = player_data
                    existing.b50_list = b50_list
                    existing.ajc_list = ajc_list
                    existing.ajc_count = ajc_count
                    existing.force_result = force_result
                    existing.updated_at = datetime.now()
                    logger.info(f"Updated data for UUID: {uuid[:8]}...")
                else:
                    # 创建新记录
                    new_data = UserData(
                        uuid=uuid,
                        player_data=player_data,
                        b50_list=b50_list,
                        ajc_list=ajc_list,
                        ajc_count=ajc_count,
                        force_result=force_result,
                    )
                    session.add(new_data)
                    logger.info(f"Created new data for UUID: {uuid[:8]}...")
                
                await session.commit()
                return True
                
        except Exception as e:
            logger.error(f"Failed to save user data: {e}")
            return False
    
    @staticmethod
    async def get_user_data(uuid: str) -> Optional[Dict]:
        """根据UUID获取用户数据"""
        try:
            async with DatabaseManager.get_session() as session:
                from sqlalchemy import select
                stmt = select(UserData).where(UserData.uuid == uuid)
                result = await session.execute(stmt)
                user_data = result.scalar_one_or_none()
                
                if not user_data:
                    logger.warning(f"Data not found for UUID: {uuid[:8]}...")
                    return None
                
                logger.info(f"Retrieved data for UUID: {uuid[:8]}...")
                return user_data.to_dict()
                
        except Exception as e:
            logger.error(f"Failed to get user data: {e}")
            return None
    
    @staticmethod
    async def delete_user_data(uuid: str) -> bool:
        """根据UUID删除用户数据"""
        try:
            async with DatabaseManager.get_session() as session:
                from sqlalchemy import delete
                stmt = delete(UserData).where(UserData.uuid == uuid)
                result = await session.execute(stmt)
                await session.commit()
                
                if result.rowcount > 0:
                    logger.info(f"Deleted data for UUID: {uuid[:8]}...")
                    return True
                else:
                    logger.warning(f"No data found for UUID: {uuid[:8]}...")
                    return False
                
        except Exception as e:
            logger.error(f"Failed to delete user data: {e}")
            return False
    
    @staticmethod
    async def get_data_stats() -> Dict:
        """获取数据统计信息"""
        try:
            async with DatabaseManager.get_session() as session:
                from sqlalchemy import select, func
                
                # 总记录数
                count_stmt = select(func.count()).select_from(UserData)
                total_count = await session.scalar(count_stmt)
                
                # 统计数据
                stats_stmt = select(
                    func.avg(UserData.ajc_count).label('avg_ajc'),
                    func.avg(UserData.force_result).label('avg_force'),
                    func.max(UserData.updated_at).label('latest_update')
                )
                stats = await session.execute(stats_stmt)
                stats_result = stats.first()
                
                return {
                    "total_records": total_count or 0,
                    "avg_ajc_count": round(stats_result.avg_ajc or 0, 2),
                    "avg_force_result": round(stats_result.avg_force or 0, 3),
                    "latest_update": stats_result.latest_update.isoformat() if stats_result.latest_update else None,
                }
                
        except Exception as e:
            logger.error(f"Failed to get data stats: {e}")
            return {}
    
    @staticmethod
    async def list_all_uuids() -> List[str]:
        """列出所有UUID"""
        try:
            async with DatabaseManager.get_session() as session:
                from sqlalchemy import select
                stmt = select(UserData.uuid)
                result = await session.execute(stmt)
                uuids = result.scalars().all()
                return list(uuids)
                
        except Exception as e:
            logger.error(f"Failed to list UUIDs: {e}")
            return []
    
    @staticmethod
    async def check_uuid_exists(uuid: str) -> bool:
        """检查UUID是否存在"""
        try:
            async with DatabaseManager.get_session() as session:
                from sqlalchemy import select
                stmt = select(UserData).where(UserData.uuid == uuid)
                result = await session.execute(stmt)
                return result.scalar_one_or_none() is not None
                
        except Exception as e:
            logger.error(f"Failed to check UUID existence: {e}")
            return False