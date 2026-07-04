# src/user_data.py
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, Text
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class UserData(Base):
    __tablename__ = "user_data"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, index=True, nullable=False)  # UUID 作为唯一标识
    player_data = Column(JSON, nullable=False)     # 存储玩家信息
    b50_list = Column(JSON, nullable=False)        # 存储B50数据
    ajc_list = Column(JSON, nullable=False)        # 存储AJC数据
    ajc_count = Column(Integer, default=0)
    force_result = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    
    def to_dict(self):
        """转换为字典"""
        return {
            "uuid": self.uuid,
            "player_data": self.player_data,
            "b50_list": self.b50_list,
            "ajc_list": self.ajc_list,
            "ajc_count": self.ajc_count,
            "force_result": self.force_result,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }