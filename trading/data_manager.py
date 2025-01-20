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
from longport.openapi import Period, AdjustType, QuoteContext  # 添加 QuoteContext 导入

class DataManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 数据存储根目录
        self.base_dir = Path('/home/options_trading/data')  # 项目根目录下
        
        # 各类数据目录
        self.data_dir = self.base_dir / 'market_data'
        self.kline_dir = self.data_dir / 'klines'      # K线数据
        self.cache_dir = self.data_dir / 'cache'       # 缓存数据
        self.logs_dir = self.data_dir / 'logs'         # 数据日志
        
        # 确保目录存在并设置正确的权限
        for directory in [self.base_dir, self.data_dir, self.kline_dir, self.cache_dir, self.logs_dir]:
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o755)  # rwxr-xr-x
        
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

    async def update_klines(self, symbol: str, quote_ctx: QuoteContext) -> bool:
        """更新K线数据"""
        try:
            # 检查是否需要更新
            now = datetime.now(self.tz)
            last_update = self.last_update.get(symbol)
            
            if last_update and (now - last_update).seconds < self.update_interval:
                return True
            
            # VIX指数使用正确的代码
            if symbol == 'VIX.US':
                symbol = 'VXX.US'  # 直接使用 VXX ETN
            
            # 获取K线数据
            try:
                candlesticks = await quote_ctx.candlesticks(
                    symbol=symbol,
                    period=Period.Day,
                    count=30,
                    adjust_type=AdjustType.NoAdjust
                )
                
                if not candlesticks:
                    self.logger.warning(f"未获取到K线数据 ({symbol})")
                    return False
                    
                # 转换为DataFrame
                data = []
                for candle in candlesticks:
                    data.append({
                        'time': datetime.fromtimestamp(candle.timestamp),
                        'open': float(candle.open),
                        'high': float(candle.high),
                        'low': float(candle.low),
                        'close': float(candle.close),
                        'volume': int(candle.volume),
                        'turnover': float(candle.turnover)
                    })
                
                df = pd.DataFrame(data)
                
                # 计算波动率
                returns = df['close'].pct_change().dropna()
                if not returns.empty:
                    volatility = float(returns.std() * np.sqrt(252))  # 年化波动率
                    df['volatility'] = volatility
                
                # 保存数据
                symbol_clean = symbol.replace('$', '').replace('^', '')
                file_path = self.get_kline_path(symbol_clean)
                df.to_csv(file_path, index=False)
                
                # 更新缓存
                self.kline_cache[symbol_clean] = df
                self.last_update[symbol_clean] = now
                
                # 特殊处理VIX数据
                if symbol == 'VXX.US':
                    self.vix_level = float(df.iloc[-1]['close'])
                    self.logger.info(f"已更新VIX数据: {self.vix_level}")
                
                return True
                
            except Exception as e:
                if 'invalid symbol' in str(e):
                    self.logger.error(f"无效的交易代码 ({symbol})")
                raise
                
        except Exception as e:
            self.logger.error(f"更新K线数据出错 ({symbol}): {str(e)}")
            if symbol == 'VXX.US':
                # VIX获取失败时设置一个默认值
                self.vix_level = 20.0
                self.logger.warning(f"VIX数据获取失败，使用默认值: {self.vix_level}")
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

    def get_vix_level(self) -> float:
        """获取最新的VIX水平"""
        return getattr(self, 'vix_level', None) 