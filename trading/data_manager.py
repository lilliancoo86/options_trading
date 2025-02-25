"""
数据管理模块 - 实时数据处理版本
主要负责实时行情数据的获取和处理
"""
import asyncio
import json
import logging
import os
import pandas as pd
import pytz
import shutil
from datetime import datetime, timedelta
from dotenv import load_dotenv
from longport.openapi import (
    Period, AdjustType, QuoteContext, Config, SubType,
    OpenApiException, PushQuote
)
from typing import Dict, List, Any, Optional

from config.config import (
    API_CONFIG, DATA_DIR
)
from trading.time_checker import TimeChecker
from trading.risk_checker import RiskChecker


class DataManager:
    def __init__(self, config: Dict[str, Any]):
        """初始化数据管理器"""
        # 加载环境变量
        load_dotenv()
        
        if not isinstance(config, dict):
            raise ValueError("配置必须是字典类型")
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.tz = pytz.timezone('America/New_York')
        
        # 移除在初始化时创建 risk_checker 的代码
        self.risk_checker = None  # 将在后续设置
        
        # 交易标的配置处理
        try:
            if 'TRADING_CONFIG' in config and isinstance(config['TRADING_CONFIG'], dict):
                trading_config = config['TRADING_CONFIG']
                if 'symbols' in trading_config and isinstance(trading_config['symbols'], list):
                    self.symbols = trading_config['symbols'].copy()  # 创建副本
                    self.logger.info(f"从 TRADING_CONFIG 中获取交易标的: {self.symbols}")
                else:
                    raise ValueError("TRADING_CONFIG 中缺少有效的 symbols 配置")
            elif 'symbols' in config and isinstance(config['symbols'], list):
                self.symbols = config['symbols'].copy()  # 创建副本
                self.logger.info(f"从配置中获取交易标的: {self.symbols}")
            else:
                raise ValueError("无法获取有效的交易标的列表")
            
            # 验证并清理交易标的
            self.symbols = [
                symbol.strip() for symbol in self.symbols 
                if isinstance(symbol, str) and symbol.strip() and symbol.endswith('.US')
            ]
            
            if not self.symbols:
                raise ValueError("没有有效的交易标的")
            
            # 确保没有重复
            self.symbols = list(dict.fromkeys(self.symbols))
            
            self.logger.info(f"已验证 {len(self.symbols)} 个有效交易标的")
            
        except Exception as e:
            self.logger.error(f"初始化交易标的时出错: {str(e)}")
            raise
        
        # 数据存储路径配置
        self.data_dir = DATA_DIR
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
        
        # 验证 API 配置
        if not self.api_config:
            raise ValueError("API_CONFIG 未配置")
        
        required_api_keys = [
            'app_key', 'app_secret', 'access_token',
            'http_url', 'quote_ws_url', 'trade_ws_url'
        ]
        missing_keys = [key for key in required_api_keys if not self.api_config.get(key)]
        if missing_keys:
            raise ValueError(f"API_CONFIG 缺少必要的配置项: {missing_keys}")
        
        # 添加详细的初始化日志
        self.logger.info(f"初始化 DataManager，已配置 {len(self.symbols)} 个交易标的")
        self.logger.debug(f"交易标的列表: {self.symbols}")
        self.logger.debug(f"API配置状态: {self.api_config is not None}")
        self.logger.debug(f"环境变量检查:")
        self.logger.debug(f"  APP_KEY: {'已设置' if os.getenv('LONGPORT_APP_KEY') else '未设置'}")
        self.logger.debug(f"  APP_SECRET: {'已设置' if os.getenv('LONGPORT_APP_SECRET') else '未设置'}")
        self.logger.debug(f"  ACCESS_TOKEN: {'已设置' if os.getenv('LONGPORT_ACCESS_TOKEN') else '未设置'}")
        
        # 断点5: 检查 LongPort 配置初始化
        try:
            self.longport_config = Config(
                app_key=self.api_config['app_key'],
                app_secret=self.api_config['app_secret'],
                access_token=self.api_config['access_token'],
                http_url=self.api_config['http_url'],
                quote_ws_url=self.api_config['quote_ws_url'],
                trade_ws_url=self.api_config['trade_ws_url']
            )
            self.logger.info("LongPort配置初始化成功")
        except Exception as e:
            self.logger.error(f"初始化LongPort配置失败: {str(e)}")
            raise
        
        # 打印配置状态（注意不要打印敏感信息）
        self.logger.debug("API配置验证:")
        self.logger.debug(f"  APP_KEY: {'已设置' if self.api_config['app_key'] else '未设置'}")
        self.logger.debug(f"  APP_SECRET: {'已设置' if self.api_config['app_secret'] else '未设置'}")
        self.logger.debug(f"  ACCESS_TOKEN: {'已设置' if self.api_config['access_token'] else '未设置'}")
        
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
        self._quote_timeout = self.api_config['quote_context']['timeout']
        self._reconnect_interval = self.api_config['quote_context']['reconnect_interval']
        self._max_retry = self.api_config['quote_context']['max_retry']
        
        # 请求限制
        self.request_limit = self.api_config['request_limit']
        self.request_times = []

    async def async_init(self) -> None:
        """异步初始化方法"""
        try:
            # 初始化行情连接
            self.logger.info("正在初始化行情连接...")
            
            # 重试机制
            max_retries = self.api_config.get('quote_context', {}).get('max_retry', 3)
            retry_interval = self.api_config.get('quote_context', {}).get('reconnect_interval', 3)
            
            for attempt in range(max_retries):
                quote_ctx = await self.ensure_quote_ctx()
                if quote_ctx is not None:
                    break
                
                if attempt < max_retries - 1:
                    self.logger.warning(f"行情连接失败，{retry_interval}秒后进行第{attempt + 2}次尝试...")
                    await asyncio.sleep(retry_interval)
            
            if quote_ctx is None:
                raise ConnectionError("初始化行情连接失败")
            
            # 订阅所有交易标的的行情
            for symbol in self.symbols:
                try:
                    # 使用同步方法进行订阅
                    quote_ctx.subscribe(
                        symbols=[symbol],
                        sub_types=[SubType.Quote, SubType.Trade, SubType.Depth],
                        is_first_push=True
                    )
                    self.logger.info(f"成功订阅 {symbol} 的行情数据")
                    await asyncio.sleep(0.1)  # 避免请求过快
                except OpenApiException as e:
                    self.logger.error(f"订阅 {symbol} 行情失败，API错误: {str(e)}")
                    continue
                except Exception as e:
                    self.logger.error(f"订阅 {symbol} 行情时发生未知错误: {str(e)}")
                    continue
            
            # 设置行情回调
            def on_quote(symbol: str, event: PushQuote):
                # 只更新缓存，不记录日志
                if symbol in self._data_cache:
                    self._data_cache[symbol]['realtime_quote'] = event
                    self._data_cache[symbol]['last_update'] = datetime.now(self.tz)
            
            quote_ctx.set_on_quote(on_quote)
            
            self.logger.info("数据管理器初始化完成")
            
        except Exception as e:
            self.logger.error(f"数据管理器初始化失败: {str(e)}")
            raise

    async def _init_historical_data(self) -> None:
        """初始化历史数据"""
        for symbol in self.symbols:
            try:
                quote_ctx = await self.ensure_quote_ctx()
                if not quote_ctx:
                    continue
                    
                # 获取最近100个交易日的数据
                klines = await quote_ctx.candlesticks(
                    symbol=symbol,
                    period=Period.Day,
                    count=100,  # 从配置中获取历史数据长度
                    adjust_type=AdjustType.ForwardAdjust
                )
                
                if not klines or not hasattr(klines, 'candlesticks'):
                    self.logger.error(f"获取 {symbol} K线数据失败")
                    continue
                    
                bars = klines.candlesticks
                if not bars:
                    self.logger.warning(f"{symbol} 没有K线数据")
                    continue
                    
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
                
                if df.empty:
                    self.logger.warning(f"{symbol} 数据为空")
                    continue
                    
                # 设置索引并排序
                df.set_index('timestamp', inplace=True)
                df.sort_index(inplace=True)
                
                # 初始化缓存字典(如果不存在)
                if symbol not in self._data_cache:
                    self._data_cache[symbol] = {}
                
                # 计算技术指标
                tech_df = self._calculate_technical_indicators(df)
                
                # 更新缓存
                self._data_cache[symbol].update({
                    'ohlcv': df,
                    'technical_indicators': tech_df,
                    'last_update': datetime.now(self.tz)
                })
                
                self.logger.info(f"成功初始化 {symbol} 的历史数据, 共 {len(df)} 条记录")
                
                # 避免请求过快
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self.logger.error(f"初始化 {symbol} 历史数据时出错: {str(e)}")
                continue

    def _calculate_technical_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标(仅保留均线相关)"""
        try:
            if df is None or df.empty:
                return df
            
            # 复制数据框以避免修改原始数据
            df = df.copy()
            
            # 确保数据按时间排序
            df.sort_index(inplace=True)
            
            # 检查必要的列是否存在
            required_columns = ['close']
            if not all(col in df.columns for col in required_columns):
                self.logger.error("数据缺少必要的列")
                return df
            
            # 检查并处理空值
            if df['close'].isnull().any():
                self.logger.warning("价格数据存在空值，使用前值填充")
                df['close'].fillna(method='ffill', inplace=True)
            
            return df
            
        except Exception as e:
            self.logger.error(f"计算技术指标时出错: {str(e)}")
            return df

    async def get_technical_data(self, symbol: str, strategy_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """获取技术分析数据并计算均线指标"""
        try:
            # 获取历史K线数据
            hist_data = await self.get_historical_data(symbol)
            if hist_data is None or hist_data.empty:
                return None
            
            # 确保timestamp是列而不是索引
            if 'timestamp' not in hist_data.columns and hist_data.index.name == 'timestamp':
                hist_data = hist_data.reset_index()
            
            # 只保留必要的列并确保它们存在
            required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            missing_columns = [col for col in required_columns if col not in hist_data.columns]
            if missing_columns:
                self.logger.error(f"{symbol} 数据缺少必要的列: {missing_columns}")
                return None
                
            hist_data = hist_data[required_columns].copy()
            
            # 将timestamp设置为索引
            hist_data.set_index('timestamp', inplace=True)
            
            # 确保数据按时间排序
            hist_data.sort_index(inplace=True)
            
            # 计算均线指标
            ma_data = self._calculate_ma_indicators(hist_data, strategy_params)
            if ma_data is None:
                return None
                
            return ma_data
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 技术分析数据时出错: {str(e)}")
            return None
            
    def _calculate_ma_indicators(self, df: pd.DataFrame, strategy_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """计算均线指标"""
        try:
            if df is None or df.empty:
                return None
                
            # 计算均线
            df['ma_fast'] = df['close'].rolling(strategy_params['ma_fast']).mean()
            df['ma_slow'] = df['close'].rolling(strategy_params['ma_slow']).mean()
            df['ma_signal'] = df['close'].rolling(strategy_params['ma_signal']).mean()
            
            # 获取最新数据
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            # 计算均线趋势信号
            signal_strength = 0.0
            
            # 均线多头排列
            if latest['ma_fast'] > latest['ma_signal'] > latest['ma_slow']:
                signal_strength = 1.0
            # 均线空头排列    
            elif latest['ma_fast'] < latest['ma_signal'] < latest['ma_slow']:
                signal_strength = -1.0
            # 其他情况计算趋势强度
            else:
                # 计算快线和慢线的差值比例
                diff_ratio = (latest['ma_fast'] - latest['ma_slow']) / latest['ma_slow']
                signal_strength = diff_ratio
                
            return {
                'data': df,
                'signal': signal_strength,
                'trend': 'bullish' if signal_strength > 0 else 'bearish',
                'strength': abs(signal_strength),
                'indicators': {
                    'ma_trend': latest['ma_fast'] > latest['ma_slow'],
                    'ma_cross': (prev['ma_fast'] - prev['ma_slow']) * 
                               (latest['ma_fast'] - latest['ma_slow']) < 0,  # 判断是否发生交叉
                    'ma_diff_ratio': (latest['ma_fast'] - latest['ma_slow']) / latest['ma_slow']
                },
                'latest': latest,
                'prev': prev
            }
            
        except Exception as e:
            self.logger.error(f"计算均线指标时出错: {str(e)}")
            return None

    async def _update_symbol_data(self, symbol: str) -> None:
        """更新单个标的数据"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return
                
            # 获取最新K线
            klines = await quote_ctx.candlesticks(
                symbol=symbol,
                period=Period.Day,
                count=1,
                adjust_type=AdjustType.ForwardAdjust
            )
            
            if klines and hasattr(klines, 'candlesticks'):
                bars = klines.candlesticks
                if bars:
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
                if not hasattr(self, '_quote_ctx') or self._quote_ctx is None:
                    try:
                        # 创建新的行情连接
                        self.logger.info("正在创建新的行情连接...")
                        
                        # 确保配置正确
                        if not hasattr(self, 'longport_config'):
                            self.logger.error("LongPort配置未初始化")
                            return None
                        
                        # 创建 QuoteContext 实例
                        self._quote_ctx = QuoteContext(self.longport_config)
                        self.logger.info("行情连接已建立")
                        
                        # 等待连接稳定
                        await asyncio.sleep(1)
                        
                        # 验证连接是否可用
                        if self.symbols:
                            test_symbol = self.symbols[0]
                            self.logger.info(f"正在使用 {test_symbol} 验证行情连接...")
                            
                            try:
                                # 尝试使用同步方法获取行情数据来验证连接
                                quote_data = self._quote_ctx.quote([test_symbol])
                                if quote_data:
                                    self.logger.info("行情连接验证成功")
                                else:
                                    self.logger.error("行情连接验证失败：未能获取行情数据")
                                    self._quote_ctx = None
                                    return None
                                    
                            except OpenApiException as e:
                                self.logger.error(f"行情连接验证失败，API错误: {str(e)}")
                                self._quote_ctx = None
                                return None
                                
                        else:
                            self.logger.warning("没有可用的交易标的进行连接验证")
                            self._quote_ctx = None
                            return None
                            
                    except Exception as e:
                        self.logger.error(f"创建行情连接时出错: {str(e)}")
                        self._quote_ctx = None
                        return None
            
            return self._quote_ctx
            
        except Exception as e:
            self.logger.error(f"确保行情连接时出错: {str(e)}")
            self._quote_ctx = None
            return None

    async def subscribe_symbols(self, symbols: List[str]) -> bool:
        """订阅行情"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                self.logger.error("无法获取行情连接")
                return False
                
            # 设置行情回调
            def on_quote(symbol: str, event: PushQuote):
                self.logger.debug(f"收到 {symbol} 的行情更新: {event}")
                # 更新数据缓存
                if symbol in self._data_cache:
                    self._data_cache[symbol]['realtime_quote'] = event
                    self._data_cache[symbol]['last_update'] = datetime.now(self.tz)
            
            quote_ctx.set_on_quote(on_quote)
            
            # 批量订阅，避免频繁请求
            batch_size = self.api_config['request_limit']['quote']['max_symbols']
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i:i + batch_size]
                try:
                    # 使用同步方法进行订阅
                    quote_ctx.subscribe(
                        symbols=batch,
                        sub_types=[SubType.Quote],
                        is_first_push=True
                    )
                    self.logger.info(f"成功订阅标的: {batch}")
                    # 订阅后等待一下，避免请求过快
                    await asyncio.sleep(0.5)
                except OpenApiException as e:
                    self.logger.error(f"订阅标的失败 {batch}: {str(e)}")
                    return False
                except Exception as e:
                    self.logger.error(f"订阅标的时发生未知错误 {batch}: {str(e)}")
                    return False
                    
            return True
            
        except Exception as e:
            self.logger.error(f"订阅行情失败: {str(e)}")
            return False

    def on_quote_update(self, symbol: str, quote: PushQuote) -> None:
        """处理实时行情推送的同步方法"""
        try:
            # 创建异步任务处理更新
            asyncio.create_task(self._handle_quote_update(symbol, quote))
        except Exception as e:
            self.logger.error(f"处理行情推送时出错: {str(e)}")

    async def _handle_quote_update(self, symbol: str, quote: PushQuote) -> None:
        """处理实时行情更新的异步方法"""
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
            
            if (self._data_cache[symbol]['ohlcv'] is not None and 
                not self._data_cache[symbol]['ohlcv'].empty):
                last_date = self._data_cache[symbol]['ohlcv'].index[-1].date()
                
                if current_date > last_date:
                    await self._update_symbol_data(symbol)
                
        except Exception as e:
            self.logger.error(f"处理 {symbol} 实时行情更新时出错: {str(e)}")

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

    async def update_all_klines(self) -> bool:
        """更新所有交易标的的K线数据"""
        try:
            success = True
            for symbol in self.symbols:
                try:
                    quote_ctx = await self.ensure_quote_ctx()
                    if not quote_ctx:
                        self.logger.error(f"无法获取行情连接，跳过更新 {symbol} 的K线数据")
                        success = False
                        continue
                    
                    # 获取当前时间
                    now = datetime.now(self.tz)
                    
                    try:
                        # 移除 await，因为 candlesticks 不是异步方法
                        klines = quote_ctx.candlesticks(
                            symbol=symbol,
                            period=Period.Day,
                            count=30,  # 获取最近30天的数据
                            adjust_type=AdjustType.ForwardAdjust
                        )
                        
                        if klines:  # 检查响应是否为空
                            # 更新数据缓存
                            df = pd.DataFrame([{
                                'timestamp': bar.timestamp,
                                'open': bar.open,
                                'high': bar.high,
                                'low': bar.low,
                                'close': bar.close,
                                'volume': bar.volume,
                                'turnover': bar.turnover
                            } for bar in klines])
                            
                            if not df.empty:
                                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.tz_convert(self.tz)
                                df.set_index('timestamp', inplace=True)
                                
                                if symbol not in self._data_cache:
                                    self._data_cache[symbol] = {}
                                
                                self._data_cache[symbol]['ohlcv'] = df
                                self._data_cache[symbol]['last_update'] = now
                                
                                self.logger.info(f"成功更新 {symbol} 的K线数据")
                                
                                # 保存到文件
                                await self._save_market_data(symbol, df)
                            else:
                                self.logger.warning(f"{symbol} K线数据转换后为空")
                                success = False
                        else:
                            self.logger.warning(f"获取 {symbol} 的K线数据为空")
                            success = False
                    
                    except OpenApiException as e:
                        self.logger.error(f"获取 {symbol} K线数据时发生API错误: {str(e)}")
                        success = False
                    
                    # 避免请求过快
                    await asyncio.sleep(1.0)
                    
                except Exception as e:
                    self.logger.error(f"更新 {symbol} K线数据时出错: {str(e)}")
                    success = False
            
            return success
            
        except Exception as e:
            self.logger.error(f"更新所有K线数据时出错: {str(e)}")
            return False

    async def _save_market_data(self, symbol: str, df: pd.DataFrame) -> None:
        """保存市场数据到文件"""
        try:
            # 确保时间戳是时区感知的
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert(self.tz)
            
            # 生成文件名 - 使用时区感知的时间
            date_str = datetime.now(self.tz).strftime(self.date_fmt)
            filename = f"{symbol}_{date_str}.csv"
            filepath = self.market_data_dir / filename
            
            # 在保存之前转换时间戳为字符串，使用统一的格式
            df_to_save = df.copy()
            
            # 保存原始时区信息
            timezone_info = df_to_save.index.tz.zone
            
            # 转换为UTC时间并格式化为ISO格式字符串
            df_to_save.index = df_to_save.index.tz_convert('UTC').strftime('%Y-%m-%d %H:%M:%S+00:00')
            
            # 添加元数据列
            df_to_save['original_timezone'] = timezone_info
            df_to_save['data_timestamp'] = datetime.now(pytz.UTC).isoformat()
            
            # 保存数据，包含时区信息
            df_to_save.to_csv(filepath)
            self.logger.debug(f"已保存 {symbol} 的市场数据到 {filepath}")
            
            # 创建备份
            backup_path = self.backup_dir / filename
            shutil.copy2(filepath, backup_path)
            
        except Exception as e:
            self.logger.error(f"保存 {symbol} 的市场数据时出错: {str(e)}")

    # 定期检查连接状态
    async def check_connection_status(self):
        quote_ctx = await self.ensure_quote_ctx()
        self.logger.info(f"行情连接状态: {quote_ctx is not None}")

    async def recover_from_error(self):
        self.logger.info("尝试从错误中恢复...")
        await self.async_init()

    async def get_latest_quote(self, symbol: str) -> Dict[str, Any]:
        """获取标的最新行情"""
        try:
            # 确保行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                self.logger.error("无法获取行情连接")
                return None

            # 获取实时行情
            try:
                # 直接调用 quote 方法
                quote_resp = quote_ctx.quote([symbol])
                if not quote_resp:
                    self.logger.error(f"获取 {symbol} 行情失败")
                    return None
                
                # 提取第一条行情数据
                quote = quote_resp[0] if quote_resp else None
                if not quote:
                    return None
                
                # 格式化行情数据，只包含确定存在的字段
                return {
                    'symbol': quote.symbol,
                    'last_done': float(quote.last_done),
                    'prev_close': float(quote.prev_close),
                    'open': float(quote.open),
                    'high': float(quote.high),
                    'low': float(quote.low),
                    'timestamp': quote.timestamp,
                    'volume': int(quote.volume),
                    'turnover': float(quote.turnover)
                }
                
            except OpenApiException as e:
                self.logger.error(f"获取 {symbol} 行情时出错: {str(e)}")
                return None
                
        except Exception as e:
            self.logger.error(f"获取 {symbol} 最新行情时出错: {str(e)}")
            return None

    async def update_market_data(self, symbol: str) -> bool:
        """更新单个标的的市场数据"""
        try:
            # 检查缓存是否需要更新
            if symbol in self._data_cache:
                last_update = self._data_cache[symbol].get('last_update')
                if last_update:
                    time_diff = (datetime.now(self.tz) - last_update).seconds
                    if time_diff < 60:  # 1分钟内的数据不更新
                        return True
            
            # 获取行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                self.logger.error(f"无法获取 {symbol} 的行情连接")
                return False
            
            now = datetime.now(self.tz)
            
            try:
                # 添加请求延迟
                await asyncio.sleep(0.2)  # 每个请求间隔200ms
                
                # 调用 candlesticks 方法
                klines = quote_ctx.candlesticks(
                    symbol=symbol,
                    period=Period.Day,
                    count=30,  # 获取最近30天的数据
                    adjust_type=AdjustType.ForwardAdjust
                )
                
                if klines:  # 检查响应是否为空
                    # 更新数据缓存
                    df = pd.DataFrame([{
                        'timestamp': bar.timestamp,
                        'open': float(bar.open),
                        'high': float(bar.high),
                        'low': float(bar.low),
                        'close': float(bar.close),
                        'volume': int(bar.volume),
                        'turnover': float(bar.turnover)
                    } for bar in klines])
                    
                    if not df.empty:
                        # 转换时间戳并设置时区
                        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s', utc=True)
                        df['timestamp'] = df['timestamp'].dt.tz_convert(self.tz)
                        df.set_index('timestamp', inplace=True)
                        
                        # 更新缓存
                        if symbol not in self._data_cache:
                            self._data_cache[symbol] = {}
                        
                        self._data_cache[symbol]['ohlcv'] = df
                        self._data_cache[symbol]['last_update'] = now
                        
                        self.logger.info(f"成功更新 {symbol} 的K线数据")
                        
                        # 保存到文件
                        await self._save_market_data(symbol, df)
                        return True
                    else:
                        self.logger.warning(f"{symbol} K线数据转换后为空")
                        return False
                else:
                    self.logger.warning(f"获取 {symbol} 的K线数据为空")
                    return False
                
            except OpenApiException as e:
                if "301606" in str(e):  # 请求频率限制错误
                    self.logger.warning(f"请求频率限制，等待后重试: {str(e)}")
                    await asyncio.sleep(1)  # 等待1秒后重试
                    return await self.update_market_data(symbol)
                else:
                    self.logger.error(f"获取 {symbol} K线数据时发生API错误: {str(e)}")
                    return False
            
        except Exception as e:
            self.logger.error(f"更新 {symbol} 数据时出错: {str(e)}")
            return False

    async def get_historical_data(self, symbol: str, days: int = 100) -> Optional[pd.DataFrame]:
        """获取历史数据"""
        try:
            # 首先检查缓存
            if symbol in self._data_cache:
                cache_data = self._data_cache[symbol].get('ohlcv')
                if cache_data is not None:
                    # 检查数据是否足够新
                    last_update = self._data_cache[symbol].get('last_update')
                    if last_update and (datetime.now(self.tz) - last_update).seconds < 300:  # 5分钟内的数据
                        return cache_data

            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            try:
                # 获取历史K线数据
                klines = await quote_ctx.candlesticks(
                    symbol=symbol,
                    period=Period.Day,
                    count=days,
                    adjust_type=AdjustType.ForwardAdjust
                )
                
                if not klines or not hasattr(klines, 'candlesticks'):
                    self.logger.error(f"获取 {symbol} K线数据失败")
                    return None
                    
                bars = klines.candlesticks
                if not bars:
                    self.logger.warning(f"{symbol} 没有K线数据")
                    return None
                
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
                
                if df.empty:
                    self.logger.warning(f"{symbol} 数据为空")
                    return None
                
                # 设置索引并排序
                df.set_index('timestamp', inplace=True)
                df.sort_index(inplace=True)
                
                # 更新缓存
                if symbol not in self._data_cache:
                    self._data_cache[symbol] = {}
                    
                self._data_cache[symbol].update({
                    'ohlcv': df,
                    'last_update': datetime.now(self.tz)
                })
                
                self.logger.info(f"成功获取 {symbol} 的历史数据, 共 {len(df)} 条记录")
                return df
                
            except OpenApiException as e:
                if "301606" in str(e):  # 请求频率限制错误
                    self.logger.warning(f"请求频率限制，等待后重试: {str(e)}")
                    await asyncio.sleep(1)  # 等待1秒后重试
                    return await self.get_historical_data(symbol, days)
                else:
                    self.logger.error(f"获取 {symbol} K线数据时发生API错误: {str(e)}")
                    return None
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 历史数据时出错: {str(e)}")
            return None

    async def get_market_value(self, symbol: str) -> Optional[float]:
        """获取标的市值"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            # 获取实时行情
            quote = await self.get_latest_quote(symbol)
            if not quote:
                return None
            
            # 获取持仓数量
            position = await self.get_position(symbol)
            if not position:
                return 0.0
            
            # 计算市值
            market_value = float(quote['last_done']) * float(position['quantity'])
            return market_value
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 市值时出错: {str(e)}")
            return None

    async def get_unrealized_pnl(self, symbol: str) -> Optional[float]:
        """获取未实现盈亏"""
        try:
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return None
            
            # 获取实时行情
            quote = await self.get_latest_quote(symbol)
            if not quote:
                return None
            
            # 获取持仓信息
            position = await self.get_position(symbol)
            if not position:
                return 0.0
            
            # 计算未实现盈亏
            current_price = float(quote['last_done'])
            avg_cost = float(position.get('avg_cost', 0))
            quantity = float(position.get('quantity', 0))
            
            unrealized_pnl = (current_price - avg_cost) * quantity
            return unrealized_pnl
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 未实现盈亏时出错: {str(e)}")
            return None

    async def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取持仓信息"""
        try:
            if not self.position_manager:
                self.logger.warning("未初始化持仓管理器")
                return None
            
            # 从持仓管理器获取持仓信息
            position = await self.position_manager.get_position(symbol)
            if position:
                return {
                    'symbol': symbol,
                    'quantity': float(position.get('quantity', 0)),
                    'avg_cost': float(position.get('avg_cost', 0)),
                    'market_value': float(position.get('market_value', 0)),
                    'unrealized_pnl': float(position.get('unrealized_pnl', 0))
                }
            
            return None
            
        except Exception as e:
            self.logger.error(f"获取 {symbol} 持仓信息时出错: {str(e)}")
            return None

    async def prepare_trade_signal(self, symbol: str, strategy_params: Dict[str, Any]) -> Dict[str, Any]:
        """准备交易信号"""
        try:
            signal = {
                'symbol': symbol,
                'asset_type': 'stock',  # 或者 'option'
                'stop_loss_pct': -0.03,  # 3%止损，注意是负数
                'take_profit_pct': 0.05,  # 5%止盈，注意是正数
                # ... 其他信号参数 ...
            }
            
            # 确保止损止盈百分比符合风险限制
            if not await self.risk_checker._validate_stop_loss_take_profit(signal, signal['asset_type']):
                self.logger.warning(f"{symbol} 止损止盈设置被拒绝")
                return None
            
            return signal
            
        except Exception as e:
            self.logger.error(f"准备 {symbol} 交易信号时出错: {str(e)}")
            return None

    def set_risk_checker(self, risk_checker: RiskChecker) -> None:
        """设置风险检查器"""
        self.risk_checker = risk_checker

    async def cleanup(self):
        """清理资源"""
        try:
            # 关闭所有数据连接
            if hasattr(self, 'quote_context'):
                await self.quote_context.close()
            
            # 保存必要的数据
            await self._save_state()
            
            self.logger.info("数据管理器清理完成")
        except Exception as e:
            self.logger.error(f"清理数据管理器时出错: {str(e)}")
