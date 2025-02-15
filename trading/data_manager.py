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
from dotenv import load_dotenv
from longport.openapi import (
    Period, AdjustType, QuoteContext, Config, SubType, 
    TradeContext, OpenApiException, PushQuote
)
import asyncio
import time
from collections import deque
from config.config import API_CONFIG

class DataManager:
    def __init__(self, config: Dict[str, Any]):
        """初始化数据管理器"""
        # 加载环境变量
        load_dotenv()
        
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # API配置
        self.api_config = API_CONFIG
        self.longport_config = Config(
            app_key=os.getenv('LONGPORT_APP_KEY'),
            app_secret=os.getenv('LONGPORT_APP_SECRET'),
            access_token=os.getenv('LONGPORT_ACCESS_TOKEN'),
            http_url=self.api_config['http_url'],
            quote_ws_url=self.api_config['quote_ws_url'],
            trade_ws_url=self.api_config['trade_ws_url']
        )
        
        # 交易标的
        self.symbols = config.get('symbols', [])
        
        # 实时数据缓存
        self._quote_cache = {
            symbol: {
                'last_price': None,
                'volume': None,
                'turnover': None,
                'timestamp': None,
                'quotes': deque(maxlen=100),  # 保留最近100个报价
                'trades': deque(maxlen=50),   # 保留最近50笔成交
                'depth': None                  # 最新盘口数据
            } for symbol in self.symbols
        }
        
        # 连接管理
        self._quote_ctx: Optional[QuoteContext] = None
        self._quote_ctx_lock = asyncio.Lock()
        self._last_quote_time = 0
        self._quote_timeout = 60
        
    async def async_init(self) -> None:
        """异步初始化"""
        try:
            # 初始化行情连接
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                raise ConnectionError("初始化行情连接失败")
            
            # 设置行情推送回调
            quote_ctx.set_on_quote(self._quote_handler)
            
            # 订阅行情
            await self.subscribe_symbols(self.symbols)
            
            self.logger.info("数据管理器初始化完成")
            
        except Exception as e:
            self.logger.error(f"数据管理器初始化失败: {str(e)}")
            raise

    def _quote_handler(self, push_quote: PushQuote) -> None:
        """处理行情推送"""
        try:
            symbol = push_quote.symbol
            if symbol not in self._quote_cache:
                return
                
            # 更新缓存
            cache = self._quote_cache[symbol]
            cache['last_price'] = push_quote.last_done
            cache['volume'] = push_quote.volume
            cache['turnover'] = push_quote.turnover
            cache['timestamp'] = push_quote.timestamp
            
            # 添加到报价队列
            cache['quotes'].append({
                'price': push_quote.last_done,
                'volume': push_quote.volume,
                'timestamp': push_quote.timestamp
            })
            
            # 计算实时指标
            self._calculate_real_time_indicators(symbol)
            
        except Exception as e:
            self.logger.error(f"处理行情推送时出错: {str(e)}")

    def _calculate_real_time_indicators(self, symbol: str) -> None:
        """计算实时技术指标"""
        try:
            cache = self._quote_cache[symbol]
            quotes = list(cache['quotes'])
            if len(quotes) < 2:
                return
                
            # 计算价格变化
            current_price = quotes[-1]['price']
            prev_price = quotes[-2]['price']
            price_change = (current_price - prev_price) / prev_price
            
            # 计算成交量变化
            volume_ratio = quotes[-1]['volume'] / sum(q['volume'] for q in quotes[-10:]) * 10
            
            # 更新缓存中的指标
            cache['indicators'] = {
                'price_change': price_change,
                'volume_ratio': volume_ratio,
                'timestamp': quotes[-1]['timestamp']
            }
            
        except Exception as e:
            self.logger.error(f"计算实时指标时出错: {str(e)}")

    async def get_real_time_data(self, symbol: str) -> Optional[Dict]:
        """获取实时数据"""
        try:
            if symbol not in self._quote_cache:
                return None
                
            cache = self._quote_cache[symbol]
            return {
                'last_price': cache['last_price'],
                'volume': cache['volume'],
                'turnover': cache['turnover'],
                'timestamp': cache['timestamp'],
                'indicators': cache.get('indicators', {}),
                'depth': cache['depth']
            }
            
        except Exception as e:
            self.logger.error(f"获取实时数据时出错: {str(e)}")
            return None

    async def ensure_quote_ctx(self) -> Optional[QuoteContext]:
        """确保行情连接可用"""
        try:
            async with self._quote_ctx_lock:
                current_time = time.time()
                
                # 检查是否需要重新连接
                if (self._quote_ctx is None or 
                    current_time - self._last_quote_time > self._quote_timeout):
                    
                    # 关闭旧连接
                    if self._quote_ctx:
                        try:
                            await self._quote_ctx.close()
                        except Exception as e:
                            self.logger.warning(f"关闭旧连接时出错: {str(e)}")
                    
                    try:
                        # 创建新连接前等待
                        await asyncio.sleep(1)
                        
                        # 创建新连接
                        self._quote_ctx = QuoteContext(self.longport_config)
                        self._last_quote_time = current_time
                        
                        self.logger.info("成功创建新的行情连接")
                        
                    except OpenApiException as e:
                        self.logger.error(f"创建行情连接失败: {str(e)}")
                        self._quote_ctx = None
                        return None
                        
                    except Exception as e:
                        self.logger.error(f"创建行情连接时出错: {str(e)}")
                        self._quote_ctx = None
                        return None
                
                return self._quote_ctx
                
        except Exception as e:
            self.logger.error(f"确保行情连接时出错: {str(e)}")
            return None

    async def subscribe_symbols(self, symbols: List[str]) -> bool:
        """订阅行情"""
        try:
            if not symbols:
                return True
                
            quote_ctx = await self.ensure_quote_ctx()
            if not quote_ctx:
                return False
            
            # 批量订阅
            try:
                await quote_ctx.subscribe(
                    symbols=symbols,
                    sub_types=[SubType.Quote, SubType.Trade, SubType.Depth],
                    is_first_push=True
                )
                self.logger.info(f"成功订阅标的: {', '.join(symbols)}")
                return True
                
            except OpenApiException as e:
                self.logger.error(f"订阅行情失败: {str(e)}")
                return False
                
        except Exception as e:
            self.logger.error(f"订阅标的时出错: {str(e)}")
            return False
