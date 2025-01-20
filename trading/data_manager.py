"""
数据管理模块
负责历史数据的存储、加载和更新
"""
from typing import Dict, Any, List, Optional
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import os
import json
from pathlib import Path

class DataManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 数据存储路径
        self.data_dir = Path(config.get('data_dir', 'data'))
        self.kline_dir = self.data_dir / 'klines'
        self.cache_dir = self.data_dir / 'cache'
        
        # 确保目录存在
        self.kline_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # 数据缓存
        self.kline_cache = {}
        self.last_update = {}
        
        # 更新间隔（秒）
        self.update_interval = config.get('update_interval', 60)

    def get_kline_path(self, symbol: str) -> Path:
        """获取K线数据文件路径"""
        return self.kline_dir / f"{symbol.replace('.', '_')}_daily.csv"

    async def load_klines(self, symbol: str) -> pd.DataFrame:
        """加载K线数据"""
        try:
            file_path = self.get_kline_path(symbol)
            if file_path.exists():
                df = pd.read_csv(file_path)
                df['time'] = pd.to_datetime(df['time'])
                df.set_index('time', inplace=True)
                return df
            return pd.DataFrame()
            
        except Exception as e:
            self.logger.error(f"加载K线数据出错 ({symbol}): {str(e)}")
            return pd.DataFrame()

    async def update_klines(self, symbol: str, quote_ctx) -> bool:
        """更新K线数据"""
        try:
            # 检查是否需要更新
            now = datetime.now(self.tz)
            last_update = self.last_update.get(symbol)
            if (last_update and 
                (now - last_update).total_seconds() < self.update_interval):
                return True

            # 加载现有数据
            df = await self.load_klines(symbol)
            
            # 确定需要请求的数据范围
            if df.empty:
                start_time = now - timedelta(days=90)  # 首次加载90天数据
            else:
                start_time = df.index[-1]
            
            # 获取新数据
            candlesticks = await quote_ctx.candlesticks(
                symbol=symbol,
                period="day",
                count=30,
                adjust_type="no_adjust"
            )
            
            if not candlesticks:
                return False
                
            # 转换为DataFrame
            new_data = pd.DataFrame([{
                'time': k.timestamp,
                'open': float(k.open),
                'high': float(k.high),
                'low': float(k.low),
                'close': float(k.close),
                'volume': int(k.volume),
                'turnover': float(k.turnover)
            } for k in candlesticks])
            
            if new_data.empty:
                return True
                
            new_data['time'] = pd.to_datetime(new_data['time'])
            new_data.set_index('time', inplace=True)
            
            # 合并数据
            if df.empty:
                df = new_data
            else:
                df = pd.concat([df, new_data])
                df = df[~df.index.duplicated(keep='last')]
                df.sort_index(inplace=True)
            
            # 保存数据
            file_path = self.get_kline_path(symbol)
            df.to_csv(file_path)
            
            # 更新缓存
            self.kline_cache[symbol] = df
            self.last_update[symbol] = now
            
            return True
            
        except Exception as e:
            self.logger.error(f"更新K线数据出错 ({symbol}): {str(e)}")
            return False

    async def get_latest_klines(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """获取最新的K线数据"""
        try:
            df = self.kline_cache.get(symbol)
            if df is None:
                df = await self.load_klines(symbol)
                self.kline_cache[symbol] = df
            
            if df.empty:
                return df
                
            # 返回指定天数的数据
            end_time = df.index[-1]
            start_time = end_time - timedelta(days=days)
            return df[df.index >= start_time]
            
        except Exception as e:
            self.logger.error(f"获取最新K线数据出错 ({symbol}): {str(e)}")
            return pd.DataFrame()

    def save_cache(self, name: str, data: Any):
        """保存缓存数据"""
        try:
            file_path = self.cache_dir / f"{name}.json"
            with open(file_path, 'w') as f:
                json.dump(data, f)
        except Exception as e:
            self.logger.error(f"保存缓存数据出错 ({name}): {str(e)}")

    def load_cache(self, name: str) -> Optional[Any]:
        """加载缓存数据"""
        try:
            file_path = self.cache_dir / f"{name}.json"
            if file_path.exists():
                with open(file_path, 'r') as f:
                    return json.load(f)
            return None
        except Exception as e:
            self.logger.error(f"加载缓存数据出错 ({name}): {str(e)}")
            return None 