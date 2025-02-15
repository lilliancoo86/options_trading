"""
数据管理模块
负责历史数据的存储、加载和更新
"""
from typing import Dict, List, Any, Optional, Union, Tuple
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import os
import json
from pathlib import Path
from longport.openapi import Period, AdjustType, QuoteContext, Config, SubType, TradeContext, OpenApiException
import asyncio
import time
from scipy.stats import percentileofscore
import traceback
from config.config import API_CONFIG

class DataManager:
    def __init__(self, config: Dict[str, Any]):
        """初始化数据管理器"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 使用 API_CONFIG
        self.api_config = API_CONFIG
        
        # 添加 longport_config 属性
        self.longport_config = Config(
            app_key=os.getenv('LONGPORT_APP_KEY'),
            app_secret=os.getenv('LONGPORT_APP_SECRET'),
            access_token=os.getenv('LONGPORT_ACCESS_TOKEN'),
            http_url=self.api_config['http_url'],
            quote_ws_url=self.api_config['quote_ws_url'],
            trade_ws_url=self.api_config['trade_ws_url']
        )
        
        # 添加 time_checker 属性
        self.time_checker = None  # 将在 async_init 中初始化
        
        # 从配置文件获取交易标的
        self.symbols = config.get('symbols', [])
        self.logger.info(f"初始化交易标的: {self.symbols}")
        
        # 连接管理
        self._quote_ctx_lock = asyncio.Lock()
        self._quote_ctx = None
        self._trade_ctx = None
        self._trade_ctx_lock = asyncio.Lock()  # 确保初始化
        self._last_quote_time = 0
        self._quote_timeout = self.api_config['quote_context']['timeout']
        self._trade_timeout = 30  # 添加交易超时设置，单位为秒
        
        # 初始化请求限制
        self.request_times = []
        self.request_limit = self.api_config.get('request_limit', {
            'max_requests': 120,  # 每分钟最大请求数
            'time_window': 60     # 时间窗口（秒）
        })
        
        # 设置数据目录，添加默认值处理
        self.base_dir = Path(config['DATA_CONFIG'].get('base_dir', '/home/options_trading/data'))
        self.market_data_dir = Path(config['DATA_CONFIG'].get('market_data_dir', self.base_dir / 'market_data'))
        self.options_data_dir = Path(config['DATA_CONFIG'].get('options_data_dir', self.base_dir / 'options_data'))
        self.historical_dir = Path(config['DATA_CONFIG'].get('historical_dir', self.base_dir / 'historical'))
        self.backup_dir = Path(config['DATA_CONFIG'].get('backup_dir', self.base_dir / 'backup'))
        
        # 添加 kline_dir 初始化
        self.kline_dir = self.market_data_dir / 'klines'
        self.cache_dir = self.market_data_dir / 'cache'
        self.logs_dir = self.market_data_dir / 'logs'
        
        # 期权数据存储路径 - 移到这里
        self.options_data = {
            'chains': self.options_data_dir / 'chains',      # 期权链数据目录
            'greeks': self.options_data_dir / 'greeks',      # 希腊字母数据目录  
            'iv_history': self.options_data_dir / 'iv',      # 隐含波动率历史目录
            'volume': self.options_data_dir / 'volume'       # 成交量数据目录
        }
        
        # 创建必要的目录
        self._init_directories()
        
        # 期权数据配置，添加默认值
        self.options_config = config['DATA_CONFIG'].get('options_data', {
            'greeks_update_interval': 300,
            'chain_update_interval': 1800,
            'iv_history_days': 30,
            'volume_threshold': 100
        })
        
        # 数据缓存
        self.kline_cache = {}
        self.last_update = {}
        self.update_interval = config['DATA_CONFIG'].get('update_interval', 60)  # 更新间隔(秒)
        
        # 创建期权数据子目录
        for directory in self.options_data.values():
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o755)

        # 添加缓存字典
        self._cache = {
            'klines': {},      # K线数据缓存
            'indicators': {},  # 技术指标缓存
            'quotes': {},     # 实时报价缓存
            'options': {}     # 期权数据缓存
        }
        
        # 缓存配置
        self.cache_config = {
            'klines_ttl': 300,    # K线数据缓存时间(秒)
            'quote_ttl': 10,      # 报价缓存时间(秒)
            'option_ttl': 60,     # 期权数据缓存时间(秒)
            'max_cache_size': 100  # 每个缓存最大条目数
        }

        # 添加技术指标参数
        self.tech_params = config.get('tech_params', {
            'ma_periods': [5, 10, 20],
            'macd': {
                'fast': 12,
                'slow': 26,
                'signal': 9
            },
            'rsi_period': 14,
            'volume_ma': 20
        })

    def _init_directories(self):
        """初始化所有必要的数据目录"""
        try:
            directories = [
                self.base_dir,
                self.market_data_dir,
                self.options_data_dir,
                self.historical_dir,
                self.backup_dir,
                self.kline_dir,
                self.cache_dir,
                self.logs_dir,
                *self.options_data.values()
            ]
            
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                os.chmod(directory, 0o755)
            
            self.logger.info("数据目录初始化完成")
            
        except Exception as e:
            self.logger.error(f"初始化数据目录时出错: {str(e)}")
            raise

    async def async_init(self):
        """异步初始化"""
        try:
            # 初始化目录
            self._init_directories()
            
            # 初始化时间检查器
            from trading.time_checker import TimeChecker
            self.time_checker = TimeChecker(self.config)
            await self.time_checker.async_init()
            
            # 初始化数据清理器
            from trading.data_cleaner import DataCleaner
            self.data_cleaner = DataCleaner(self.config)
            await self.data_cleaner.async_init()
            
            # 初始化行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                raise ConnectionError("初始化行情连接失败")
            
            # 启动数据更新任务
            asyncio.create_task(self.start_data_update())
            asyncio.create_task(self.data_cleaner.start_cleanup_task())
            
            self.logger.info("数据管理器初始化完成")
            return self
            
        except Exception as e:
            self.logger.error(f"数据管理器初始化失败: {str(e)}")
            raise

    async def ensure_quote_ctx(self) -> Optional[QuoteContext]:
        """确保行情连接可用"""
        try:
            # 检查 symbols 是否为空
            if not self.symbols:
                self.logger.error("交易标的列表为空")
                return None
            
            async with self._quote_ctx_lock:
                if self._quote_ctx is None:
                    try:
                        # 创建新的行情连接
                        self._quote_ctx = QuoteContext(self.longport_config)
                        
                        # 等待连接建立
                        await asyncio.sleep(3)  # 等待连接建立
                        
                        # 验证连接是否成功
                        try:
                            # 使用正确的方式调用 quote 方法
                            test_symbol = self.symbols[0]
                            quotes = await self._quote_ctx.quote([test_symbol])  # 获取行情数据
                            
                            # 验证返回的数据
                            if quotes and len(quotes) > 0:
                                self.logger.info(f"行情连接验证成功 (测试标的: {test_symbol})")
                                self._last_quote_time = time.time()
                            else:
                                raise ValueError("未能获取有效的行情数据")
                                
                        except Exception as e:
                            self.logger.error(f"行情连接验证失败: {str(e)}")
                            if self._quote_ctx:
                                try:
                                    await self._quote_ctx.close()
                                except:
                                    pass
                            self._quote_ctx = None
                            raise
                            
                    except Exception as e:
                        self.logger.error(f"创建行情连接时出错: {str(e)}")
                        if self._quote_ctx:
                            try:
                                await self._quote_ctx.close()
                            except:
                                pass
                        self._quote_ctx = None
                        raise
                
                elif time.time() - self._last_quote_time > self._quote_timeout:
                    # 重新连接逻辑
                    try:
                        # 关闭旧连接
                        if self._quote_ctx:
                            try:
                                await self._quote_ctx.close()
                            except:
                                pass
                        
                        # 创建新连接
                        self._quote_ctx = QuoteContext(self.longport_config)
                        await asyncio.sleep(2)  # 等待连接建立
                        
                        # 验证新连接
                        test_symbol = self.symbols[0]
                        quotes = await self._quote_ctx.quote([test_symbol])
                        
                        if quotes and len(quotes) > 0:
                            self.logger.info("已重新建立行情连接")
                            self._last_quote_time = time.time()
                        else:
                            raise ValueError("未能获取有效的行情数据")
                        
                    except Exception as e:
                        self.logger.error(f"重新连接失败: {str(e)}")
                        self._quote_ctx = None
                        raise
                
                return self._quote_ctx
                
        except Exception as e:
            self.logger.error(f"确保行情连接时出错: {str(e)}")
            return None

    def get_kline_path(self, symbol: str) -> Path:
        """获取K线数据文件路径"""
        return self.kline_dir / f"{symbol.replace('.', '_')}_daily.csv"

    async def load_klines(self, symbol: str) -> pd.DataFrame:
        """加载K线数据"""
        try:
            file_path = self.get_kline_path(symbol)
            if file_path.exists():
                df = pd.read_csv(file_path)
                if not df.empty:
                    df['time'] = pd.to_datetime(df['time'])
                    df.set_index('time', inplace=True)
                    return df
                else:
                    self.logger.warning(f"{symbol} 的K线数据文件为空")
            else:
                self.logger.info(f"{symbol} 的K线数据文件不存在，尝试重新获取数据")
                success = await self.update_klines(symbol)
                if success:
                    return await self.load_klines(symbol)
            
            return pd.DataFrame()
            
        except Exception as e:
            self.logger.error(f"加载K线数据出错 ({symbol}): {str(e)}")
            return pd.DataFrame()

    async def update_klines(self, symbol: str, period: Period = Period.Day) -> Optional[List[Dict]]:
        """更新K线数据"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            # 使用 history_candlesticks_by_offset 替代 history_candlesticks_by_date
            candlesticks = await quote_ctx.history_candlesticks_by_offset(
                symbol=symbol,
                period=period,
                adjust_type=AdjustType.Forward,    # 前复权
                forward=True,                      # 向前获取
                count=1000                         # 获取1000条数据
            )
            
            if not candlesticks:
                return None
            
            # 转换为字典列表
            return [{
                'timestamp': k.timestamp,
                'open': k.open,
                'high': k.high,
                'low': k.low,
                'close': k.close,
                'volume': k.volume,
                'turnover': k.turnover
            } for k in candlesticks]
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} K线数据时出错: {str(e)}")
            return None

    async def get_latest_klines(self, symbol: str, days: int = 30) -> pd.DataFrame:
        """获取最新的K线数据"""
        try:
            # 先尝试从文件加载数据
            df = await self.load_klines(symbol)
            
            if df.empty:
                # 如果文件不存在，则从API获取数据
                df = await self.get_klines(symbol)
                if not df.empty:
                    # 保存到文件
                    file_path = self.get_kline_path(symbol)
                    df.to_csv(file_path)
            
            if df.empty:
                return pd.DataFrame()
            
            # 返回指定天数的数据
            return df.tail(days)
            
        except Exception as e:
            self.logger.error(f"获取最新K线数据出错 ({symbol}): {str(e)}")
            return pd.DataFrame()

    async def get_klines(self, symbol: str) -> Optional[pd.DataFrame]:
        """获取K线数据"""
        try:
            # 检查请求频率
            if not await self.check_rate_limit():
                return None
            
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            try:
                # 正确处理同步调用
                candlesticks = quote_ctx.history_candlesticks_by_offset(
                    symbol=symbol,
                    period=Period.Day,
                    adjust_type=AdjustType.NoAdjust,
                    forward=True,
                    count=100
                )
                
                if not candlesticks:
                    return None
                    
                # 转换为DataFrame
                data = [{
                    'timestamp': k.timestamp,
                    'open': k.open,
                    'high': k.high,
                    'low': k.low,
                    'close': k.close,
                    'volume': k.volume,
                    'turnover': k.turnover
                } for k in candlesticks]
                
                df = pd.DataFrame(data)
                df = self._calculate_indicators(df)
                
                return df
                
            except OpenApiException as e:
                if e.code == 429002:  # API请求频率限制
                    self.logger.warning(f"API请求频率限制，等待后重试: {str(e)}")
                    await asyncio.sleep(2)
                    return None
                else:
                    raise
                
        except Exception as e:
            self.logger.error(f"获取 {symbol} K线数据时出错: {str(e)}")
            return None

    async def get_latest_quote(self, symbol: str) -> Optional[pd.Series]:
        """获取最新报价"""
        try:
            # 检查请求限制
            await self.check_rate_limit()
            
            # 检查标的是否在监控列表中
            if symbol not in self.symbols:
                self.logger.warning(f"标的 {symbol} 不在监控列表中")
                return None
            
            # 获取行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                raise ValueError("行情连接未就绪")
            
            # 等待连接就绪
            await asyncio.sleep(1)
            
            try:
                # 使用 quote 方法获取报价（同步方法）
                quotes = quote_ctx.quote([symbol])
                if quotes and len(quotes) > 0:
                    quote = quotes[0]
                    return pd.Series({
                        'symbol': symbol,
                        'last_done': float(quote.last_done),
                        'prev_close': float(quote.prev_close),
                        'open': float(quote.open),
                        'high': float(quote.high),
                        'low': float(quote.low),
                        'timestamp': quote.timestamp,
                        'volume': int(quote.volume),
                        'turnover': float(quote.turnover)
                    })
                else:
                    # 如果无法获取实时报价，尝试从K线数据获取
                    df = await self.get_klines(symbol)
                    if not df:
                        return None
                    latest_data = df[-1]
                    prev_close = df[-2]['close'] if len(df) > 1 else latest_data['close']
                    return pd.Series({
                        'symbol': symbol,
                        'last_done': float(latest_data['close']),
                        'prev_close': float(prev_close),
                        'open': float(latest_data['open']),
                        'high': float(latest_data['high']),
                        'low': float(latest_data['low']),
                        'timestamp': latest_data['timestamp'],
                        'volume': int(latest_data['volume']),
                        'turnover': float(latest_data['turnover'])
                    })
            except Exception as e:
                self.logger.error(f"获取实时报价出错 ({symbol}): {str(e)}")
            
            return None
            
        except Exception as e:
            self.logger.error(f"获取最新报价出错 ({symbol}): {str(e)}")
            return None

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

    async def update_all_klines(self) -> bool:
        """更新所有K线数据"""
        try:
            success = True
            for symbol in self.symbols:
                try:
                    if not await self.update_klines(symbol):
                        success = False
                except Exception as e:
                    self.logger.error(f"更新 {symbol} K线数据时出错: {str(e)}")
                    success = False
            return success
        except Exception as e:
            self.logger.error(f"更新所有K线数据时出错: {str(e)}")
            return False

    async def check_rate_limit(self) -> bool:
        """检查请求频率限制"""
        try:
            current_time = time.time()
            
            # 清理过期的请求记录
            self.request_times = [t for t in self.request_times 
                                if current_time - t < self.api_config['request_limit']['time_window']]
            
            # 检查是否超过限制
            if len(self.request_times) >= self.api_config['request_limit']['max_requests']:
                wait_time = self.request_times[0] + self.api_config['request_limit']['time_window'] - current_time
                if wait_time > 0:
                    self.logger.warning(f"达到请求限制，等待 {wait_time:.1f} 秒")
                    await asyncio.sleep(wait_time)
                    return False
                
            # 行情接口特殊限制
            quote_times = [t for t in self.request_times 
                          if current_time - t < self.api_config['request_limit']['quote']['time_window']]
            if len(quote_times) >= self.api_config['request_limit']['quote']['max_requests']:
                wait_time = 1.0  # 行情接口固定等待1秒
                self.logger.warning(f"达到行情接口限制，等待 {wait_time} 秒")
                await asyncio.sleep(wait_time)
                return False
            
            # 记录新的请求时间
            self.request_times.append(current_time)
            return True
            
        except Exception as e:
            self.logger.error(f"检查请求限制时出错: {str(e)}")
            return False

    async def get_market_data(self) -> Dict[str, Any]:
        """获取市场数据"""
        try:
            result = {
                'volatility': 0.0,
                'quotes': {}
            }
            
            current_time = time.time()
            
            # 更新所有标的的K线数据
            for symbol in self.symbols:
                try:
                    # 更新K线数据
                    df = await self.get_klines(symbol)
                    if df is None or not df:
                        continue
                    
                    # 获取实时报价
                    quote_data = await self.get_latest_quote(symbol)
                    if quote_data is None:
                        continue
                    
                    # 构建标的数据
                    symbol_data = {
                        'quote': quote_data.to_dict(),
                        'technical': {
                            'close': df['close'],
                            'volume': df['volume'],
                            'high': df['high'],
                            'low': df['low'],
                            'ma5': df['ma5'].tolist() if 'ma5' in df.columns else [],
                            'ma20': df['ma20'].tolist() if 'ma20' in df.columns else [],
                            'rsi': df['rsi'].tolist() if 'rsi' in df.columns else [],
                            'macd': df['macd'].tolist() if 'macd' in df.columns else [],
                            'signal': df['signal'].tolist() if 'signal' in df.columns else []
                        }
                    }
                    
                    # 添加到结果中
                    result['quotes'][symbol] = symbol_data
                    
                    # 使用数据中的波动率
                    if 'volatility' in df.columns:
                        result['volatility'] = float(df.iloc[-1]['volatility'])
                    
                except Exception as e:
                    self.logger.error(f"获取 {symbol} 市场数据时出错: {str(e)}")
                    continue
            
            return result
            
        except Exception as e:
            self.logger.error(f"获取市场数据时出错: {str(e)}")
            return {
                'volatility': 0.0,
                'quotes': {}
            }

    async def close(self):
        """关闭数据管理器"""
        try:
            if self.time_checker:
                await self.time_checker.close()
            if self._quote_ctx:
                try:
                    # 手动清理资源
                    self._quote_ctx = None
                except Exception as e:
                    self.logger.warning(f"关闭行情连接时出错: {str(e)}")
            
            if self._trade_ctx:
                try:
                    # 手动清理资源
                    self._trade_ctx = None
                except Exception as e:
                    self.logger.warning(f"关闭交易连接时出错: {str(e)}")
                
            self.logger.info("数据管理器已关闭")
        except Exception as e:
            self.logger.error(f"关闭数据管理器时出错: {str(e)}")

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取标的报价，使用缓存"""
        try:
            # 检查缓存
            cache_data = self._cache['quotes'].get(symbol)
            if cache_data and time.time() - cache_data['time'] < self.cache_config['quote_ttl']:
                return cache_data['data']

            # 获取行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                raise ValueError("行情连接未就绪")

            # 获取报价
            quotes = await quote_ctx.quote([symbol])
            if not quotes:
                return None

            quote_data = quotes[0]
            
            # 更新缓存
            self._cache['quotes'][symbol] = {
                'data': quote_data,
                'time': time.time()
            }
            
            return quote_data

        except Exception as e:
            self.logger.error(f"获取 {symbol} 报价时出错: {str(e)}")
            return None

    async def _get_trade_ctx(self):
        """获取交易连接"""
        try:
            async with self._trade_ctx_lock:
                current_time = time.time()
                
                if (self._trade_ctx is None or 
                    current_time - self._last_trade_time > self._trade_timeout):
                    
                    self._trade_ctx = None
                    
                    try:
                        # 创建新连接前等待
                        await asyncio.sleep(1)
                        
                        # 创建新连接
                        self._trade_ctx = TradeContext(self.longport_config)
                        self._last_trade_time = current_time
                        
                        # 等待连接就绪
                        await asyncio.sleep(1)
                        
                    except Exception as e:
                        self.logger.error(f"创建交易连接失败: {str(e)}")
                        self._trade_ctx = None
                        raise
                
                return self._trade_ctx
                
        except Exception as e:
            self.logger.error(f"获取交易连接时出错: {str(e)}")
            return None

    async def get_option_chain(self, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """获取期权链数据"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            # 获取期权到期日列表
            expiry_dates = await quote_ctx.option_chain_expiry_date_list(symbol)
            if not expiry_dates:
                return None
            
            all_contracts = []
            for date in expiry_dates:
                # 获取该到期日的期权链
                chain = await quote_ctx.option_chain(
                    symbol=symbol,
                    expiry_date=date
                )
                if chain and chain.option_chain:
                    all_contracts.extend(chain.option_chain)
                await asyncio.sleep(0.1)  # 添加延迟避免请求过快
            
            return all_contracts if all_contracts else None
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 期权链时出错: {str(e)}")
            return None

    def _filter_option_chain(self, chain_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """过滤和增强期权链数据"""
        try:
            filtered = []
            for contract in chain_data:
                # 基本条件筛选
                if not (contract['volume'] >= self.options_config['volume_threshold'] and
                       contract['open_interest'] >= self.options_config['min_open_interest']):
                    continue
                    
                # 增加合约评估指标
                contract.update({
                    'time_value': self._calculate_time_value(contract),
                    'moneyness': self._calculate_moneyness(contract),
                    'liquidity_score': self._calculate_liquidity_score(contract),
                    'iv_percentile': self._calculate_iv_percentile(contract),
                    'gamma_exposure': self._calculate_gamma_exposure(contract)
                })
                
                filtered.append(contract)
                
            return filtered
            
        except Exception as e:
            self.logger.error(f"过滤期权链数据时出错: {str(e)}")
            return []

    def _calculate_time_value(self, contract: Dict) -> float:
        """计算时间价值"""
        try:
            intrinsic_value = max(0, contract['strike_price'] - contract['underlying_price'])
            time_value = contract['last_done'] - intrinsic_value
            return max(0, time_value)
        except Exception as e:
            self.logger.error(f"计算时间价值时出错: {str(e)}")
            return 0

    def _calculate_iv_percentile(self, contract: Dict) -> float:
        """计算IV分位数"""
        try:
            iv_history = self._get_iv_history(contract['symbol'])
            if iv_history is None:
                return 50.0
            return percentileofscore(iv_history, contract['implied_volatility'])
        except Exception as e:
            self.logger.error(f"计算IV分位数时出错: {str(e)}")
            return 50.0

    async def get_positions(self) -> Optional[List[Dict[str, Any]]]:
        """获取持仓数据，使用缓存"""
        try:
            # 检查缓存
            cache_data = self._cache['positions'].get('all')
            if cache_data and time.time() - cache_data['time'] < self.cache_config['positions']:
                return cache_data['data']

            # 获取交易连接
            trade_ctx = await self._get_trade_ctx()
            if not trade_ctx:
                raise ValueError("交易连接未就绪")

            # 获取持仓
            positions = await trade_ctx.positions()
            
            # 更新缓存
            self._cache['positions']['all'] = {
                'data': positions,
                'time': time.time()
            }
            
            return positions

        except Exception as e:
            self.logger.error(f"获取持仓数据时出错: {str(e)}")
            return None

    async def process_klines(self, symbol: str) -> bool:
        """处理K线数据"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                raise ValueError("行情连接未就绪")
            
            # 使用 history_candlesticks_by_offset 获取K线数据
            klines = await quote_ctx.history_candlesticks_by_offset(
                symbol=symbol,
                period=Period.Day,
                adjust_type=AdjustType.NoAdjust,
                forward=True,
                count=100  # 获取最近100根K线
            )
            
            if not klines:
                self.logger.warning(f"未获取到 {symbol} 的K线数据")
                return False
            
            # 将K线数据转换为DataFrame
            data = []
            for k in klines:
                data.append({
                    'timestamp': k.timestamp,
                    'open': k.open,
                    'high': k.high,
                    'low': k.low,
                    'close': k.close,
                    'volume': k.volume,
                    'turnover': k.turnover
                })
            
            df = pd.DataFrame(data)
            
            if df.empty:
                self.logger.warning(f"{symbol} K线数据为空")
                return False
            
            # 验证必要的列
            required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                self.logger.error(f"K线数据验证失败: 缺少必要列: {missing_columns}")
                return False
            
            # 设置时间索引
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            # 计算技术指标
            df = self._calculate_technical_indicators(df)
            
            # 保存数据到文件
            file_path = self.get_kline_path(symbol)
            df.to_csv(file_path)
            self.logger.info(f"已保存 {symbol} 的K线数据到文件")
            
            return True
            
        except Exception as e:
            self.logger.error(f"处理 {symbol} K线数据时出错: {str(e)}")
            return False

    def _calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        try:
            # 移动平均线
            for period in [5, 10, 20]:
                df[f'MA{period}'] = df['close'].rolling(window=period).mean()
            
            # MACD
            exp1 = df['close'].ewm(span=12, adjust=False).mean()
            exp2 = df['close'].ewm(span=26, adjust=False).mean()
            df['MACD'] = exp1 - exp2
            df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
            df['Hist'] = df['MACD'] - df['Signal']
            
            # RSI
            delta = df['close'].diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            df['RSI'] = 100 - (100 / (1 + rs))
            
            # 波动率
            df['volatility'] = df['close'].rolling(window=20).std()
            
            # 其他指标...
            
            return df
            
        except Exception as e:
            self.logger.error(f"计算技术指标时出错: {str(e)}")
            return df

    async def _validate_klines(self, df: pd.DataFrame) -> bool:
        """验证K线数据的完整性"""
        try:
            # 检查必要的列
            required_columns = [
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ]
            
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                self.logger.error(f"K线数据验证失败: 缺少必要列: {missing_columns}")
                return False
            
            # 检查数据类型
            expected_types = {
                'timestamp': 'datetime64[ns]',
                'open': ['float64', 'float32'],
                'high': ['float64', 'float32'],
                'low': ['float64', 'float32'],
                'close': ['float64', 'float32'],
                'volume': ['int64', 'float64'],
                'turnover': ['float64', 'float32']
            }
            
            for col, expected_type in expected_types.items():
                if isinstance(expected_type, list):
                    if df[col].dtype.name not in expected_type:
                        df[col] = df[col].astype(expected_type[0])
                elif df[col].dtype.name != expected_type:
                    df[col] = df[col].astype(expected_type)
            
            # 检查是否有空值
            if df.isnull().any().any():
                self.logger.warning("K线数据中存在空值，将使用前向填充方法处理")
                df.fillna(method='ffill', inplace=True)
            
            # 检查数据量是否足够
            min_records = self.config.get('min_kline_records', 20)
            if len(df) < min_records:
                self.logger.error(f"K线数据量不足: {len(df)} < {min_records}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"验证K线数据时出错: {str(e)}")
            return False

    async def update_market_data(self, symbol: str) -> bool:
        """更新市场数据"""
        try:
            # 检查请求频率
            current_time = time.time()
            self.request_times = [t for t in self.request_times if current_time - t < 60]
            if len(self.request_times) >= self.api_config['request_limit']['max_requests']:
                await asyncio.sleep(2)  # 超过限制时等待
                return False
            
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return False
            
            # 记录请求时间
            self.request_times.append(current_time)
            
            try:
                # 使用正确的API方法获取数据
                candlesticks = await quote_ctx.history_candlesticks_by_offset(
                    symbol=symbol,
                    period=Period.Day,
                    adjust_type=AdjustType.NoAdjust,
                    forward=True,
                    count=100
                )
                
                if not candlesticks:
                    self.logger.warning(f"未获取到 {symbol} 的K线数据")
                    return False
                    
                # 处理数据
                data = [{
                    'timestamp': k.timestamp,
                    'open': k.open,
                    'high': k.high,
                    'low': k.low,
                    'close': k.close,
                    'volume': k.volume,
                    'turnover': k.turnover
                } for k in candlesticks]
                
                # 计算技术指标
                df = pd.DataFrame(data)
                df = self._calculate_indicators(df)
                
                # 添加延迟避免请求过快
                await asyncio.sleep(0.5)
                
                return True
                
            except OpenApiException as e:
                if e.code == 429002:  # API请求频率限制
                    self.logger.warning(f"API请求频率限制，等待后重试: {str(e)}")
                    await asyncio.sleep(2)
                    return False
                else:
                    self.logger.error(f"获取 {symbol} 市场数据时出错: {str(e)}")
                    return False
                
        except Exception as e:
            self.logger.error(f"更新 {symbol} 市场数据时出错: {str(e)}")
            return False

    async def start_data_update(self):
        """启动数据更新任务"""
        while True:
            try:
                update_tasks = [self.update_market_data(symbol) for symbol in self.symbols]
                results = await asyncio.gather(*update_tasks, return_exceptions=True)
                
                # 检查更新结果
                failed_symbols = [
                    symbol for symbol, result in zip(self.symbols, results)
                    if isinstance(result, Exception) or not result
                ]
                if failed_symbols:
                    self.logger.warning(f"以下标的更新失败: {failed_symbols}")
                    
                await asyncio.sleep(300)  # 5分钟更新一次
                
            except Exception as e:
                self.logger.error(f"数据更新任务出错: {str(e)}")
                await asyncio.sleep(60)

    def _is_cache_valid(self, cache_type: str, symbol: str) -> bool:
        """检查缓存是否有效"""
        try:
            if cache_type not in self._cache or symbol not in self._cache[cache_type]:
                return False
                
            cache_data = self._cache[cache_type][symbol]
            current_time = time.time()
            
            # 检查缓存是否过期
            if current_time - cache_data['time'] > self.cache_config[f'{cache_type}_ttl']:
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"检查缓存有效性时出错: {str(e)}")
            return False

    async def subscribe_quote(self, symbols: List[str], sub_types: List[SubType] = None) -> bool:
        """订阅行情数据"""
        try:
            if not symbols:
                return True
            
            if not sub_types:
                sub_types = [SubType.Quote]
            
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return False
            
            # 使用新的订阅方法
            await quote_ctx.subscribe(
                symbols=symbols,
                sub_types=sub_types
            )
            self.logger.info(f"已订阅标的: {symbols}, 类型: {[st.name for st in sub_types]}")
            return True
            
        except Exception as e:
            self.logger.error(f"订阅标的时出错: {str(e)}")
            return False

    async def subscribe_symbols(self, symbols: List[str]) -> bool:
        """订阅标的"""
        try:
            if not symbols:
                return True
            
            # 每次最多订阅20个标的
            batch_size = 20
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                try:
                    quote_ctx = await self.ensure_quote_ctx()
                    if not quote_ctx:
                        return False
                        
                    # 使用正确的订阅方法和参数
                    await quote_ctx.subscribe(
                        symbols=batch,
                        sub_types=[SubType.Quote, SubType.Depth, SubType.Trade],
                        is_first_push=True
                    )
                    
                    self.logger.info(f"成功订阅标的批次: {batch}")
                    await asyncio.sleep(1)  # 添加延迟避免请求过快
                    
                except OpenApiException as e:
                    if e.code == 429002:  # API请求频率限制
                        self.logger.warning(f"API请求频率限制，等待后重试: {str(e)}")
                        await asyncio.sleep(2)
                        continue
                    else:
                        self.logger.error(f"订阅标的批次失败: {str(e)}")
                        continue
                except Exception as e:
                    self.logger.error(f"订阅标的批次失败: {str(e)}")
                    continue
                
            return True
            
        except Exception as e:
            self.logger.error(f"订阅标的时出错: {str(e)}")
            return False