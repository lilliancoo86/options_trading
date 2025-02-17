"""
数据管理模块 - 实时数据处理版本
主要负责实时行情数据的获取和处理
"""
from typing import Dict, List, Any, Optional, Union, Tuple
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import os
from pathlib import Path
from dotenv import load_dotenv
from longport.openapi import (
    Period, AdjustType, QuoteContext, Config, SubType, 
    TradeContext, OpenApiException, PushQuote
)
import asyncio
import time
import json
from collections import deque
from config.config import API_CONFIG
from trading.time_checker import TimeChecker
import shutil

class DataManager:
    def __init__(self, config: Dict[str, Any]):
        """初始化数据管理器"""
        # 加载环境变量
        load_dotenv()
        
        if not isinstance(config, dict):
            raise ValueError("配置必须是字典类型")
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 交易标的配置处理
        if 'symbols' not in config:
            raise ValueError("配置中缺少 symbols 字段")
        
        if not isinstance(config['symbols'], list):
            raise ValueError("symbols 必须是列表类型")
        
        self.symbols = config['symbols']
        if not self.symbols:
            raise ValueError("交易标的列表不能为空")
        
        # 验证每个交易标的的格式
        for symbol in self.symbols:
            if not isinstance(symbol, str):
                raise ValueError(f"交易标的必须是字符串类型: {symbol}")
            if not symbol.endswith('.US'):
                raise ValueError(f"交易标的格式错误，必须以 .US 结尾: {symbol}")
        
        # 数据存储路径配置
        self.data_dir = Path('/home/options_trading/data')
        self.market_data_dir = self.data_dir / 'market_data'
        self.options_data_dir = self.data_dir / 'options_data'
        self.historical_dir = self.data_dir / 'historical'
        self.backup_dir = self.data_dir / 'backup'
        
        # 创建必要的目录
        for dir_path in [self.market_data_dir, self.options_data_dir, 
                        self.historical_dir, self.backup_dir]:
            dir_path.mkdir(parents=True, exist_ok=True)
            
        # 数据文件命名格式
        self.date_fmt = '%Y%m%d'
        self.datetime_fmt = '%Y%m%d_%H%M%S'
        
        # API配置
        self.api_config = API_CONFIG
        
        # 添加详细的初始化日志
        self.logger.info(f"初始化 DataManager，已配置 {len(self.symbols)} 个交易标的")
        self.logger.debug(f"交易标的列表: {self.symbols}")
        self.logger.debug(f"API配置状态: {self.api_config is not None}")
        self.logger.debug(f"环境变量检查:")
        self.logger.debug(f"  APP_KEY: {'已设置' if os.getenv('LONGPORT_APP_KEY') else '未设置'}")
        self.logger.debug(f"  APP_SECRET: {'已设置' if os.getenv('LONGPORT_APP_SECRET') else '未设置'}")
        self.logger.debug(f"  ACCESS_TOKEN: {'已设置' if os.getenv('LONGPORT_ACCESS_TOKEN') else '未设置'}")
        
        # 初始化 LongPort 配置
        self.longport_config = Config(
            app_key=self.api_config['app_key'],
            app_secret=self.api_config['app_secret'],
            access_token=self.api_config['access_token']
        )
        
        # 确保所有必需的环境变量都存在
        required_env_vars = [
            'LONGPORT_APP_KEY',
            'LONGPORT_APP_SECRET',
            'LONGPORT_ACCESS_TOKEN'
        ]
        
        missing_vars = [var for var in required_env_vars if not os.getenv(var)]
        if missing_vars:
            raise ValueError(f"缺少必需的环境变量: {', '.join(missing_vars)}")
            
        # 时间检查器
        self.time_checker = TimeChecker(config)
        
        # 数据缓存
        self._data_cache = {}
        for symbol in self.symbols:
            self._data_cache[symbol] = {
                'ohlcv': pd.DataFrame(),  # OHLCV数据
                'technical_indicators': pd.DataFrame(),  # 技术指标
                'last_update': None,  # 最后更新时间
                'realtime_quote': None  # 实时报价
            }
        
        # 连接管理
        self._quote_ctx_lock = asyncio.Lock()
        self._quote_ctx = None
        self._last_quote_time = 0
        self._quote_timeout = 60
        
        # 请求限制
        self.request_limit = self.api_config['request_limit']
        self.request_times = []

    async def async_init(self) -> None:
        """异步初始化"""
        try:
            # 初始化行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                raise ConnectionError("初始化行情连接失败")
            
            # 订阅行情
            if not await self.subscribe_symbols(self.symbols):
                raise ConnectionError("订阅行情失败")
            
            # 初始化历史数据
            await self._init_historical_data()
            
            self.logger.info("数据管理器初始化完成")
            
        except Exception as e:
            self.logger.error(f"数据管理器初始化失败: {str(e)}")
            raise

    async def _init_historical_data(self) -> None:
        """初始化历史数据"""
        for symbol in self.symbols:
            try:
                # 获取历史K线数据
                quote_ctx = await self.ensure_quote_ctx()
                if not quote_ctx:
                    continue
                    
                # 获取最近100个交易日的数据
                bars = await quote_ctx.history_candlesticks(
                    symbol=symbol,
                    period=Period.Day,
                    count=100,
                    adjust_type=AdjustType.Forward
                )
                    
                # 转换为DataFrame
                df = pd.DataFrame([{
                    'timestamp': bar.timestamp,
                    'open': bar.open,
                    'high': bar.high,
                    'low': bar.low,
                    'close': bar.close,
                    'volume': bar.volume,
                    'turnover': bar.turnover
                } for bar in bars])
                
                if not df.empty:
                    df.set_index('timestamp', inplace=True)
                    df.sort_index(inplace=True)
                    
                    # 计算技术指标
                    tech_df = self._calculate_technical_indicators(df)
            
            # 更新缓存
                    self._data_cache[symbol]['ohlcv'] = df
                    self._data_cache[symbol]['technical_indicators'] = tech_df
                    self._data_cache[symbol]['last_update'] = datetime.now(self.tz)
                    
                await asyncio.sleep(0.5)  # 避免请求过快
                
            except Exception as e:
                self.logger.error(f"初始化 {symbol} 历史数据时出错: {str(e)}")

    def _calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        try:
            tech_df = pd.DataFrame(index=df.index)
            
            # 移动平均线
            for period in [5, 10, 20]:
                tech_df[f'MA{period}'] = df['close'].rolling(window=period).mean()
            
            # MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            macd = exp1 - exp2
            signal = macd.ewm(span=9, adjust=False).mean()
            tech_df['MACD'] = macd
            tech_df['Signal'] = signal
            tech_df['Hist'] = macd - signal
            
            # RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            tech_df['RSI'] = 100 - (100 / (1 + rs))
            
            # 波动率
            tech_df['volatility'] = df['close'].rolling(window=20).std()
            
            # 价格变化
            tech_df['price_change'] = df['close'].pct_change()
            tech_df['price_std'] = tech_df['price_change'].rolling(window=20).std()
            
            # 成交量
            tech_df['volume_ratio'] = df['volume'] / df['volume'].rolling(window=20).mean()
            
            # 趋势强度
            tech_df['trend_strength'] = abs(tech_df['MA5'] - tech_df['MA20']) / tech_df['MA20']
            
            # 动量
            tech_df['momentum'] = df['close'] - df['close'].shift(10)
            tech_df['momentum_ma'] = tech_df['momentum'].rolling(window=10).mean()
            
            # 波动率Z分数
            tech_df['volatility_zscore'] = (tech_df['volatility'] - tech_df['volatility'].rolling(window=50).mean()) / tech_df['volatility'].rolling(window=50).std()
            
            return tech_df
            
        except Exception as e:
            self.logger.error(f"计算技术指标时出错: {str(e)}")
            return pd.DataFrame()

    async def get_technical_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取技术分析数据"""
        try:
            if symbol not in self._data_cache:
                return None
                
            cache = self._data_cache[symbol]
            current_time = datetime.now(self.tz)
            
            # 检查是否需要更新数据
            if (cache['last_update'] is None or 
                (current_time - cache['last_update']).seconds > 300):  # 5分钟更新一次
                
                await self._update_symbol_data(symbol)
            
            return cache['technical_indicators']
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 技术分析数据时出错: {str(e)}")
            return None

    async def _update_symbol_data(self, symbol: str) -> None:
        """更新单个标的数据"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return
                
            # 获取最新K线
            bars = await quote_ctx.history_candlesticks(
                    symbol=symbol,
                    period=Period.Day,
                count=1,
                adjust_type=AdjustType.Forward
            )
            
            if not bars:
                return
                
            latest_bar = bars[0]
            
            # 更新OHLCV数据
            new_data = pd.DataFrame([{
                'timestamp': latest_bar.timestamp,
                'open': latest_bar.open,
                'high': latest_bar.high,
                'low': latest_bar.low,
                'close': latest_bar.close,
                'volume': latest_bar.volume,
                'turnover': latest_bar.turnover
            }]).set_index('timestamp')
            
            # 更新缓存
            self._data_cache[symbol]['ohlcv'] = pd.concat([
                self._data_cache[symbol]['ohlcv'].iloc[:-1], 
                new_data
            ])
            
            # 重新计算技术指标
            tech_df = self._calculate_technical_indicators(
                self._data_cache[symbol]['ohlcv']
            )
            self._data_cache[symbol]['technical_indicators'] = tech_df
            self._data_cache[symbol]['last_update'] = datetime.now(self.tz)
            
        except Exception as e:
            self.logger.error(f"更新 {symbol} 数据时出错: {str(e)}")

    async def on_quote_update(self, symbol: str, quote: PushQuote) -> None:
        """处理实时行情更新"""
        try:
            if symbol not in self._data_cache:
                return
                
            # 更新实时报价
            self._data_cache[symbol]['realtime_quote'] = {
                'last_price': quote.last_done,
                'volume': quote.volume,
                'turnover': quote.turnover,
                'timestamp': quote.timestamp
            }
            
            # 如果是新的交易日，更新日K数据
            current_date = datetime.fromtimestamp(
                quote.timestamp, 
                self.tz
            ).date()
            last_date = self._data_cache[symbol]['ohlcv'].index[-1].date()
            
            if current_date > last_date:
                await self._update_symbol_data(symbol)
                
        except Exception as e:
            self.logger.error(f"处理 {symbol} 实时行情更新时出错: {str(e)}")

    async def ensure_quote_ctx(self) -> Optional[QuoteContext]:
        """确保行情连接可用"""
        try:
            async with self._quote_ctx_lock:
                # 检查现有连接是否可用
                if (self._quote_ctx and 
                    time.time() - self._last_quote_time < self._quote_timeout):
                    try:
                        # 测试连接是否真正可用
                        if hasattr(self._quote_ctx, 'subscribe'):
                            await self._quote_ctx.subscribe(
                                symbols=[self.symbols[0]],
                                sub_types=[SubType.Quote],
                                is_first_push=False
                            )
                            return self._quote_ctx
                    except Exception:
                        self.logger.warning("现有连接不可用，将创建新连接")
                        if self._quote_ctx:
                            try:
                                await self._quote_ctx.close()
                            except:
                                pass
                        self._quote_ctx = None
                
                # 创建新连接
                try:
                    self.logger.info("正在创建新的行情连接...")
                    
                    # 创建 QuoteContext 实例
                    self._quote_ctx = QuoteContext(self.longport_config)
                    
                    if not self._quote_ctx:
                        raise ValueError("创建 QuoteContext 失败")
                    
                    # 设置回调函数
                    self._quote_ctx.set_on_quote(self._on_quote)
                    
                    # 等待连接建立
                    await asyncio.sleep(1)
                    
                    # 测试连接
                    await self._quote_ctx.subscribe(
                        symbols=[self.symbols[0]],
                        sub_types=[SubType.Quote],
                        is_first_push=False
                    )
                    
                    self._last_quote_time = time.time()
                    self.logger.info("行情连接创建成功")
                    return self._quote_ctx
                    
                except OpenApiException as e:
                    self.logger.error(f"创建行情连接失败: {str(e)}")
                    if self._quote_ctx:
                        try:
                            await self._quote_ctx.close()
                        except:
                            pass
                    self._quote_ctx = None
                    return None
                
        except Exception as e:
            self.logger.error(f"确保行情连接时出错: {str(e)}")
            if self._quote_ctx:
                try:
                    await self._quote_ctx.close()
                except:
                    pass
            self._quote_ctx = None
            return None

    async def subscribe_symbols(self, symbols: List[str]) -> bool:
        """订阅行情"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return False
                
            # 批量订阅，避免频繁请求
            batch_size = self.api_config['request_limit']['quote']['max_symbols']
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                try:
                    await quote_ctx.subscribe(
                        symbols=batch,
                        sub_types=[SubType.Quote, SubType.Trade, SubType.Depth],
                        is_first_push=True
                    )
                    self.logger.info(f"成功订阅标的: {batch}")
                    # 订阅后等待一下，避免请求过快
                    await asyncio.sleep(0.5)
                except Exception as e:
                    self.logger.error(f"订阅标的失败 {batch}: {str(e)}")
                    return False
                    
            return True
            
        except Exception as e:
            self.logger.error(f"订阅行情失败: {str(e)}")
            return False

    def _on_quote(self, symbol: str, quote: PushQuote) -> None:
        """处理实时行情推送"""
        try:
            asyncio.create_task(self._handle_quote_update(symbol, quote))
        except Exception as e:
            self.logger.error(f"处理行情推送时出错: {str(e)}")

    async def save_kline_data(self, symbol: str, kline_data: pd.DataFrame) -> bool:
        """保存K线数据到本地"""
        try:
            if kline_data.empty:
                return False
                
            # 构建文件路径
            date_str = datetime.now(self.tz).strftime(self.date_fmt)
            file_path = self.market_data_dir / 'klines' / f"{symbol}_{date_str}.csv"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 如果文件已存在，则合并数据
            if file_path.exists():
                existing_data = pd.read_csv(file_path)
                existing_data['time'] = pd.to_datetime(existing_data['time'])
                kline_data['time'] = pd.to_datetime(kline_data['time'])
                
                # 合并并去重
                combined_data = pd.concat([existing_data, kline_data])
                combined_data = combined_data.drop_duplicates(subset=['time'])
                combined_data = combined_data.sort_values('time')
                
                # 保存合并后的数据
                combined_data.to_csv(file_path, index=False)
            else:
                # 直接保存新数据
                kline_data.to_csv(file_path, index=False)
                
            self.logger.info(f"成功保存K线数据: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存K线数据时出错: {str(e)}")
            return False

    async def save_options_data(self, symbol: str, options_data: Dict[str, Any]) -> bool:
        """保存期权数据到本地"""
        try:
            # 构建文件路径
            date_str = datetime.now(self.tz).strftime(self.date_fmt)
            file_path = self.options_data_dir / f"{symbol}_{date_str}.json"
            
            # 如果文件已存在，则读取并更新数据
            if file_path.exists():
                with open(file_path, 'r') as f:
                    existing_data = json.load(f)
                    
                # 更新数据
                existing_data.update(options_data)
                data_to_save = existing_data
            else:
                data_to_save = options_data
                
            # 保存数据
            with open(file_path, 'w') as f:
                json.dump(data_to_save, f, indent=2)
                
            self.logger.info(f"成功保存期权数据: {file_path}")
            return True
            
        except Exception as e:
            self.logger.error(f"保存期权数据时出错: {str(e)}")
            return False

    async def backup_data(self) -> bool:
        """备份数据到backup目录"""
        try:
            timestamp = datetime.now(self.tz).strftime(self.datetime_fmt)
            
            # 备份市场数据
            market_backup_dir = self.backup_dir / f"market_data_{timestamp}"
            market_backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 复制市场数据文件
            for file_path in self.market_data_dir.rglob('*.csv'):
                shutil.copy2(file_path, market_backup_dir / file_path.name)
                
            # 备份期权数据
            options_backup_dir = self.backup_dir / f"options_data_{timestamp}"
            options_backup_dir.mkdir(parents=True, exist_ok=True)
            
            # 复制期权数据文件
            for file_path in self.options_data_dir.rglob('*.json'):
                shutil.copy2(file_path, options_backup_dir / file_path.name)
                
            self.logger.info(f"成功备份数据到: {self.backup_dir}")
            return True
            
        except Exception as e:
            self.logger.error(f"备份数据时出错: {str(e)}")
            return False

    async def move_to_historical(self, days_old: int = 30) -> bool:
        """将旧数据移动到historical目录"""
        try:
            cutoff_date = datetime.now(self.tz) - timedelta(days=days_old)
            cutoff_str = cutoff_date.strftime(self.date_fmt)
            
            # 移动旧的市场数据
            for file_path in self.market_data_dir.rglob('*.csv'):
                file_date = file_path.stem.split('_')[-1]
                if file_date < cutoff_str:
                    dest_path = self.historical_dir / 'market_data' / file_path.name
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(file_path), str(dest_path))
                    
            # 移动旧的期权数据
            for file_path in self.options_data_dir.rglob('*.json'):
                file_date = file_path.stem.split('_')[-1]
                if file_date < cutoff_str:
                    dest_path = self.historical_dir / 'options_data' / file_path.name
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(file_path), str(dest_path))
                    
            self.logger.info(f"成功移动旧数据到historical目录")
            return True
            
        except Exception as e:
            self.logger.error(f"移动历史数据时出错: {str(e)}")
            return False
