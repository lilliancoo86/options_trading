"""
市场时间检查模块
"""
from typing import Dict, Any, Optional, Tuple
import logging
from datetime import datetime, time, timedelta
import pytz
import re
import os
from pathlib import Path
import json
import asyncio


class TimeChecker:
    """市场时间检查类"""
    
    # 默认市场时间配置
    DEFAULT_MARKET_TIMES = {
        'pre_market': {
            'open': time(4, 0),    # 改为 time 对象
            'close': time(9, 30)
        },
        'regular': {
            'open': time(9, 30),
            'close': time(16, 0)
        },
        'post_market': {
            'open': time(16, 0),
            'close': time(20, 0)
        },
        'force_close': time(15, 45),  # 强制平仓时间
        'warning': time(15, 40),      # 预警时间
        'close_position_minutes': 15,   # 收盘前平仓时间（分钟）
        'close_protection': {
            'enabled': True,
            'normal_close_minutes': 15,    # 正常交易日提前15分钟平仓
            'early_close_minutes': 30,     # 提前收市日提前30分钟平仓
            'force_close_minutes': 5,      # 强制平仓时间(距离收盘前5分钟)
            'min_profit_close': 0.1,       # 提前平仓的最小收益率(10%)
            'max_loss_close': -0.15        # 提前平仓的最大亏损率(-15%)
        },
        'time_stop': {
            'enabled': True,
            'max_hold_days': 3,           # 最大持仓天数
            'check_interval': 300         # 检查间隔(秒)
        }
    }

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        初始化时间检查器
        
        Args:
            config: 配置字典，可选
        """
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 使用默认配置
        self.market_times = self.DEFAULT_MARKET_TIMES.copy()
        
        # 设置收盘平仓时间
        self.close_position_time = time(
            hour=15,
            minute=60 - self.market_times['close_position_minutes']
        )
        
        # 市场状态记录
        self.status_dir = Path('/home/options_trading/data/market_status')
        self.status_dir.mkdir(parents=True, exist_ok=True)
        
        # 交易日历缓存
        self._trading_days_cache = None
        self._last_cache_update = None
        self._cache_duration = timedelta(hours=24)

    async def async_init(self) -> None:
        """
        异步初始化方法
        
        Returns:
            None
        """
        try:
            # 获取当前市场状态，验证时区设置是否正确
            status = self.get_market_status()
            self.logger.info(f"时间检查器初始化完成，当前市场状态: {status['current_time']}")
            
        except Exception as e:
            self.logger.error(f"时间检查器初始化失败: {str(e)}")
            raise

    def get_market_status(self) -> Dict[str, Any]:
        """
        获取当前市场状态
        
        Returns:
            Dict[str, Any]: 市场状态信息字典
        """
        try:
            # 确保使用正确的时区
            current_time = datetime.now(self.tz)
            current_time_only = current_time.time()
            
            # 初始化状态
            status = {
                'is_trading_day': True,
                'session': 'closed',
                'next_open': None,
                'time_to_open': None,
                'time_to_close': None,
                'should_close_positions': False,
                'current_time': current_time.strftime('%Y-%m-%d %H:%M:%S %Z')  # 添加时间信息用于调试
            }
            
            # 检查是否是交易日
            if current_time.weekday() >= 5:  # 周六日
                status['is_trading_day'] = False
                next_day = self._get_next_trading_day(current_time)
                status['next_open'] = next_day.replace(
                    hour=9, minute=30, second=0, microsecond=0
                )
                self.logger.info(f"非交易日: {status['current_time']}")
                return status
            
            # 判断当前交易时段
            if self._is_in_time_range(current_time_only, 'pre_market'):
                status['session'] = 'pre_market'
                market_open = current_time.replace(hour=9, minute=30, second=0, microsecond=0)
                status['time_to_open'] = (market_open - current_time).total_seconds() / 60
                self.logger.info(f"盘前时段: {status['current_time']}")
                
            elif self._is_in_time_range(current_time_only, 'regular'):
                status['session'] = 'regular'
                market_close = current_time.replace(hour=16, minute=0, second=0, microsecond=0)
                status['time_to_close'] = (market_close - current_time).total_seconds() / 60
                
                # 检查是否需要平仓
                if current_time_only >= self.close_position_time:
                    status['should_close_positions'] = True
                    self.logger.info(f"收盘平仓时间: {status['current_time']}")
                else:
                    self.logger.info(f"常规交易时段: {status['current_time']}")
                
            elif self._is_in_time_range(current_time_only, 'post_market'):
                status['session'] = 'post_market'
                next_day = self._get_next_trading_day(current_time)
                status['next_open'] = next_day.replace(
                    hour=9, minute=30, second=0, microsecond=0
                )
                
            else:
                self.logger.info(f"非交易时段: {status['current_time']}")
            
            return status
            
        except Exception as e:
            self.logger.error(f"获取市场状态时出错: {str(e)}")
            return {
                'is_trading_day': False,
                'session': 'unknown',
                'error': str(e),
                'current_time': datetime.now(self.tz).strftime('%Y-%m-%d %H:%M:%S %Z')
            }

    def should_close_positions(self) -> bool:
        """
        检查是否应该平仓
        
        Returns:
            bool: 是否应该平仓
        """
        try:
            status = self.get_market_status()
            return status.get('should_close_positions', False)
            
        except Exception as e:
            self.logger.error(f"检查平仓状态时出错: {str(e)}")
            return False

    def check_trading_time(self) -> bool:
        """
        检查当前是否是交易时间
        
        Returns:
            bool: 是否是交易时间
        """
        return self.can_trade()

    async def can_trade(self) -> bool:
        """
        检查当前是否可以交易
        
        Returns:
            bool: 是否可以交易
        """
        try:
            current_time = datetime.now(self.tz)
            
            # 检查是否是交易日
            if not await self.is_trading_day(current_time):
                self.logger.debug("当前不是交易日")
                return False
                
            # 获取当前时间
            current_time_only = current_time.time()
            
            # 检查是否在交易时段
            regular_start = self.market_times['regular']['open']
            regular_end = self.market_times['regular']['close']
            
            if regular_start <= current_time_only <= regular_end:
                return True
                
            # 检查是否允许盘前盘后交易
            if self.config.get('allow_extended_hours', False):
                pre_start = self.market_times['pre_market']['open']
                after_end = self.market_times['post_market']['close']
                
                if pre_start <= current_time_only <= after_end:
                    return True
                    
            return False
            
        except Exception as e:
            self.logger.error(f"检查交易时间时出错: {str(e)}")
            return False

    def _is_in_time_range(self, current_time: time, session: str) -> bool:
        """
        检查时间是否在指定的交易时段内
        
        Args:
            current_time: 当前时间
            session: 交易时段名称
            
        Returns:
            bool: 是否在交易时段内
        """
        session_hours = self.market_times.get(session)
        if not session_hours:
            return False
            
        return session_hours['open'] <= current_time < session_hours['close']

    def _get_next_trading_day(self, current_time: datetime) -> datetime:
        """
        获取下一个交易日
        
        Args:
            current_time: 当前时间
            
        Returns:
            datetime: 下一个交易日
        """
        next_day = current_time + timedelta(days=1)
        while next_day.weekday() >= 5:  # 如果是周末
            next_day += timedelta(days=1)
        return next_day

    def record_status(self) -> None:
        """
        记录当前市场状态
        """
        try:
            status = self.get_market_status()
            
            # 记录状态变化
            current_time = status['current_time']
            session = status['session']
            
            # 记录不同时段的状态
            if session == 'pre_market':
                self.logger.info(f"盘前交易时段 ({current_time})")
            elif session == 'regular':
                if status.get('should_close_positions'):
                    self.logger.warning(f"收盘平仓时间 ({current_time})")
                else:
                    self.logger.info(f"常规交易时段 ({current_time})")
            elif session == 'post_market':
                self.logger.info(f"盘后交易时段 ({current_time})")
            else:
                self.logger.info(f"市场休市 ({current_time})")
                
            # 记录距离开盘/收盘的时间
            if status.get('time_to_open'):
                self.logger.info(f"距离开盘还有 {status['time_to_open']:.0f} 分钟")
            elif status.get('time_to_close'):
                self.logger.info(f"距离收盘还有 {status['time_to_close']:.0f} 分钟")
                
            # 记录下一个交易日信息
            if status.get('next_open'):
                self.logger.info(f"下一个交易日开盘时间: {status['next_open']}")
                
        except Exception as e:
            self.logger.error(f"记录市场状态时出错: {str(e)}")

    def _parse_option_expiry(self, symbol: str) -> Optional[datetime]:
        """
        解析期权代码获取到期日
        
        Args:
            symbol: 期权代码，格式如 AAPL250117C150000.US
            
        Returns:
            Optional[datetime]: 到期日期，如果解析失败则返回None
        """
        try:
            # 检查是否是期权
            if not any(x in symbol for x in ['C', 'P']):
                return None
                
            # 提取日期部分
            # SAP250321 -> 25(年)03(月)21(日)
            match = re.search(r'([A-Z]+)(\d{2})(\d{2})(\d{2})[CP]', symbol)
            if not match:
                self.logger.error(f"期权代码格式错误: {symbol}")
                return None
                
            # 解析日期
            year = int('20' + match.group(2))  # 25 -> 2025
            month = int(match.group(3))        # 03 -> 3
            day = int(match.group(4))          # 21 -> 21
            
            # 验证日期有效性
            if not (2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
                self.logger.error(
                    f"期权到期日无效: {symbol} "
                    f"({year}-{month:02d}-{day:02d})"
                )
                return None
            
            # 创建日期对象
            expiry_date = datetime(year, month, day, 16, 0)  # 设置为当天下午4点
            expiry_date = self.tz.localize(expiry_date)
            
            self.logger.debug(
                f"解析期权到期日: {symbol} -> "
                f"{expiry_date.strftime('%Y-%m-%d %H:%M %Z')}"
            )
            
            return expiry_date
            
        except Exception as e:
            self.logger.error(f"解析期权到期日时出错: {str(e)}")
            return None

    def check_expiry_close(self, symbol: str) -> bool:
        """
        检查期权是否接近到期
        
        Args:
            symbol: 期权代码
            
        Returns:
            bool: 是否需要平仓
        """
        try:
            # 解析期权到期日
            expiry_date = self._parse_option_expiry(symbol)
            if not expiry_date:
                return False
            
            # 获取当前时间
            current_time = datetime.now(self.tz)
            
            # 计算剩余天数
            days_to_expiry = (expiry_date - current_time).days
            
            # 如果剩余天数小于等于设定值，需要平仓
            if days_to_expiry <= self.config.get('option_expiry_days', 1):
                self.logger.warning(
                    f"期权 {symbol} 接近到期 "
                    f"(还有 {days_to_expiry} 天)"
                )
                return True
            
            return False
            
        except Exception as e:
            self.logger.error(f"检查期权到期时出错: {str(e)}")
            return False

    def get_next_market_open(self) -> Optional[datetime]:
        """
        获取下一个交易日开盘时间
        
        Returns:
            Optional[datetime]: 下一个交易日开盘时间，如果出错则返回None
        """
        try:
            current_time = datetime.now(self.tz)
            
            # 如果当前是交易日且还未开盘
            if current_time.weekday() < 5:  # 周一到周五
                market_open = current_time.replace(
                    hour=9, minute=30, second=0, microsecond=0
                )
                if current_time < market_open:
                    return market_open
            
            # 获取下一个交易日
            next_day = self._get_next_trading_day(current_time)
            return next_day.replace(
                hour=9, minute=30, second=0, microsecond=0
            )
            
        except Exception as e:
            self.logger.error(f"获取下一个交易日开盘时间时出错: {str(e)}")
            return None

    def get_market_close_time(self) -> Optional[datetime]:
        """
        获取当日收盘时间
        
        Returns:
            Optional[datetime]: 收盘时间，如果出错则返回None
        """
        try:
            current_time = datetime.now(self.tz)
            
            # 如果不是交易日，返回None
            if current_time.weekday() >= 5:
                return None
                
            return current_time.replace(
                hour=16, minute=0, second=0, microsecond=0
            )
            
        except Exception as e:
            self.logger.error(f"获取收盘时间时出错: {str(e)}")
            return None

    def get_session_times(self, session: str) -> Optional[Dict[str, datetime]]:
        """
        获取指定交易时段的开始和结束时间
        
        Args:
            session: 交易时段名称 ('pre_market', 'regular', 'post_market')
            
        Returns:
            Optional[Dict[str, datetime]]: 包含开始和结束时间的字典
        """
        try:
            if session not in self.market_times:
                return None
                
            current_time = datetime.now(self.tz)
            session_hours = self.market_times[session]
            
            start_time = current_time.replace(
                hour=session_hours['open'].hour,
                minute=session_hours['open'].minute,
                second=0,
                microsecond=0
            )
            
            end_time = current_time.replace(
                hour=session_hours['close'].hour,
                minute=session_hours['close'].minute,
                second=0,
                microsecond=0
            )
            
            return {
                'start': start_time,
                'end': end_time
            }
            
        except Exception as e:
            self.logger.error(f"获取交易时段时间时出错: {str(e)}")
            return None

    def is_holiday(self, date: Optional[datetime] = None) -> bool:
        """
        检查是否是假期（目前只检查周末，后续可添加节假日）
        
        Args:
            date: 要检查的日期，默认为当前日期
            
        Returns:
            bool: 是否是假期
        """
        try:
            if date is None:
                date = datetime.now(self.tz)
                
            # 目前只检查周末
            return date.weekday() >= 5
            
        except Exception as e:
            self.logger.error(f"检查假期时出错: {str(e)}")
            return True  # 错误时返回True以确保安全

    def get_time_to_close(self) -> Optional[float]:
        """
        获取距离收盘还有多少分钟
        
        Returns:
            Optional[float]: 距离收盘的分钟数，如果已收盘或出错则返回None
        """
        try:
            current_time = datetime.now(self.tz)
            close_time = self.get_market_close_time()
            
            if not close_time or current_time >= close_time:
                return None
                
            return (close_time - current_time).total_seconds() / 60
            
        except Exception as e:
            self.logger.error(f"计算距离收盘时间时出错: {str(e)}")
            return None

    def get_time_to_session(self, session: str) -> Optional[Dict[str, float]]:
        """
        获取距离指定交易时段的开始和结束还有多少分钟
        
        Args:
            session: 交易时段名称
            
        Returns:
            Optional[Dict[str, float]]: 包含到开始和结束的分钟数
        """
        try:
            session_times = self.get_session_times(session)
            if not session_times:
                return None
                
            current_time = datetime.now(self.tz)
            
            return {
                'to_start': (session_times['start'] - current_time).total_seconds() / 60 
                    if current_time < session_times['start'] else None,
                'to_end': (session_times['end'] - current_time).total_seconds() / 60 
                    if current_time < session_times['end'] else None
            }
            
        except Exception as e:
            self.logger.error(f"计算距离交易时段时间时出错: {str(e)}")
            return None

    def get_current_session(self) -> str:
        """
        获取当前交易时段
        
        Returns:
            str: 当前交易时段 ('pre_market', 'regular', 'post_market', 'closed')
        """
        try:
            current_time = datetime.now(self.tz)
            current_time_only = current_time.time()
            
            # 检查是否是交易日
            if self.is_holiday():
                return 'closed'
            
            # 检查各个交易时段
            for session, hours in self.market_times.items():
                if hours['open'] <= current_time_only < hours['close']:
                    return session
            
            return 'closed'
            
        except Exception as e:
            self.logger.error(f"获取当前交易时段时出错: {str(e)}")
            return 'closed'

    def format_market_time(self, dt: datetime) -> str:
        """
        格式化市场时间
        
        Args:
            dt: 要格式化的时间
            
        Returns:
            str: 格式化后的时间字符串
        """
        try:
            if not dt.tzinfo:
                dt = dt.replace(tzinfo=self.tz)
            elif dt.tzinfo != self.tz:
                dt = dt.astimezone(self.tz)
                
            return dt.strftime('%Y-%m-%d %H:%M:%S %Z')
            
        except Exception as e:
            self.logger.error(f"格式化市场时间时出错: {str(e)}")
            return str(dt)

    def is_new_trading_day(self, last_time: float) -> bool:
        """
        检查是否是新的交易日
        
        Args:
            last_time: 上次交易的时间戳
            
        Returns:
            bool: 是否是新的交易日
        """
        try:
            now = datetime.now(self.tz)
            last_dt = datetime.fromtimestamp(last_time, self.tz)
            
            # 如果是不同的日期，或者是同一天但过了凌晨4点(美股收盘时间)
            return (now.date() != last_dt.date() or 
                    (now.date() == last_dt.date() and 
                     now.hour >= 4 and last_dt.hour < 4))
                     
        except Exception as e:
            self.logger.error(f"检查新交易日时出错: {str(e)}")
            return False

    def _str_to_time(self, time_str: str) -> time:
        """将时间字符串转换为 time 对象"""
        try:
            hour, minute = map(int, time_str.split(':'))
            return time(hour, minute)
        except Exception as e:
            self.logger.error(f"时间字符串转换失败: {str(e)}")
            return time(0, 0)  # 返回默认值

    async def check_close_protection(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """
        检查收盘前平仓保护
        
        Args:
            position: 持仓信息字典
            
        Returns:
            Tuple[bool, str, float]: (是否需要平仓, 平仓原因, 平仓比例)
        """
        try:
            if not self.market_times['close_protection']['enabled']:
                return False, "", 0.0

            # 获取当前时间和收盘时间
            now = datetime.now(self.tz)
            close_time = await self.get_market_close_time(now)
            if not close_time:
                return False, "", 0.0
                
            # 检查是否是交易日
            if not await self.is_trading_day(now):
                return False, "", 0.0
                
            # 计算距离收盘的分钟数
            minutes_to_close = (close_time - now).total_seconds() / 60
            
            # 获取平仓配置
            close_config = self.market_times['close_protection']
            
            # 检查是否是提前收市日
            is_early_close = await self.is_early_close_day(now)
            close_threshold = (close_config['early_close_minutes'] 
                             if is_early_close 
                             else close_config['normal_close_minutes'])
            
            # 计算持仓收益率
            cost_price = float(position.get('cost_price', 0))
            current_price = float(position.get('current_price', 0))
            profit_rate = (current_price - cost_price) / cost_price if cost_price else 0
            
            # 强制平仓检查
            if minutes_to_close <= close_config['force_close_minutes']:
                return True, "收盘前强制平仓保护", 1.0
                
            # 收益率达到目标，提前平仓
            if (minutes_to_close <= close_threshold and 
                profit_rate >= close_config['min_profit_close']):
                return True, f"收盘前获利平仓 (收益率: {profit_rate:.1%})", 1.0
                
            # 亏损超过阈值，提前平仓
            if (minutes_to_close <= close_threshold and 
                profit_rate <= close_config['max_loss_close']):
                return True, f"收盘前止损平仓 (亏损率: {profit_rate:.1%})", 1.0
                
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查收盘前平仓保护时出错: {str(e)}")
            return False, f"检查出错: {str(e)}", 0.0

    async def check_time_risk(self, position: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查时间风险"""
        try:
            # 检查最大持仓时间
            if self.DEFAULT_MARKET_TIMES['time_stop']['enabled']:
                hold_days = await self._get_position_hold_days(position)
                if hold_days >= self.DEFAULT_MARKET_TIMES['time_stop']['max_hold_days']:
                    return True, f"超过最大持仓时间 ({hold_days}天)", 1.0

            # 检查是否在交易时段
            if not await self.is_trading_time():
                return True, "非交易时段", 1.0

            return False, "", 0.0

        except Exception as e:
            self.logger.error(f"检查时间风险时出错: {str(e)}")
            return False, f"检查出错: {str(e)}", 0.0

    def _is_early_close_day(self, date: datetime) -> bool:
        """检查是否是提前收市日"""
        try:
            # 添加美股提前收市日期判断
            early_close_dates = [
                # 感恩节后的周五
                # 圣诞节前夕
                # 独立日前夕
                # 其他提前收市日期
            ]
            return date.date() in early_close_dates
            
        except Exception as e:
            self.logger.error(f"检查提前收市日时出错: {str(e)}")
            return False

    async def _get_position_hold_days(self, position: Dict[str, Any]) -> int:
        """获取持仓天数"""
        try:
            open_time = datetime.fromtimestamp(
                float(position.get('open_time', 0)), 
                self.tz
            )
            now = datetime.now(self.tz)
            return (now - open_time).days
            
        except Exception as e:
            self.logger.error(f"获取持仓天数时出错: {str(e)}")
            return 0

    async def is_trading_day(self, date: datetime) -> bool:
        """检查指定日期是否为交易日"""
        try:
            # 检查是否是周末
            if date.weekday() in [5, 6]:
                return False
                
            # 更新交易日历缓存
            await self._update_trading_days_cache()
            
            # 检查是否是假期
            date_str = date.strftime('%Y%m%d')
            if self._trading_days_cache and date_str in self._trading_days_cache.get('holidays', []):
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"检查交易日时出错: {str(e)}")
            return False
            
    async def _update_trading_days_cache(self) -> None:
        """更新交易日历缓存"""
        try:
            current_time = datetime.now(self.tz)
            
            # 检查是否需要更新缓存
            if (self._trading_days_cache is None or 
                self._last_cache_update is None or
                current_time - self._last_cache_update > self._cache_duration):
                
                cache_file = self.status_dir / 'trading_days.json'
                if cache_file.exists():
                    with open(cache_file, 'r') as f:
                        self._trading_days_cache = json.load(f)
                        
                self._last_cache_update = current_time
                
        except Exception as e:
            self.logger.error(f"更新交易日历缓存时出错: {str(e)}")

    async def check_market_time(self) -> bool:
        """
        检查当前是否在交易时间内
        
        Returns:
            bool: 是否在交易时间内
        """
        try:
            current_time = datetime.now(self.tz)
            
            # 检查是否是假期
            if self.is_holiday(current_time):
                self.logger.info(f"当前是假期: {current_time}")
                return False
            
            # 获取当前时间的 time 对象
            current_time_obj = current_time.time()
            
            # 检查是否在各个交易时段
            for session, times in self.market_times.items():
                if session in ['pre_market', 'regular', 'post_market']:
                    if times['open'] <= current_time_obj < times['close']:
                        self.logger.debug(f"当前在 {session} 交易时段")
                        return True
            
            self.logger.info(f"当前不在交易时间: {current_time}")
            return False
            
        except Exception as e:
            self.logger.error(f"检查交易时间时出错: {str(e)}")
            return False  # 出错时返回 False 以确保安全