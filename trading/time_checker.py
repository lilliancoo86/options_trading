"""
时间检查模块
负责检查交易时间和市场状态
"""
from typing import Dict, Any, Tuple
from datetime import datetime, time, timedelta
import logging
import pytz
import re

class TimeChecker:
    def __init__(self, config: Dict[str, Any], test_mode: bool = False):
        self.config = config
        self.test_mode = test_mode  # 添加测试模式标志
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 市场时间设置
        self.market_times = config.get('market_times', {
            'pre_market': {
                'open': '04:00',
                'close': '09:30'
            },
            'regular': {
                'open': '09:30',
                'close': '16:00'
            },
            'post_market': {
                'open': '16:00',
                'close': '20:00'
            },
            'force_close': '15:45',  # 收盘前15分钟强制平仓
            'warning': '15:40'       # 收盘前20分钟发出警告
        })
        
        # 假期日历
        self.holidays = config.get('holidays', [])
        
        # 交易时段设置
        self.trading_sessions = config.get('trading_sessions', ['regular'])  # 可选: pre_market, regular, post_market

    def check_force_close(self) -> Tuple[bool, str]:
        """
        检查是否需要强制平仓
        Returns:
            Tuple[bool, str]: (是否需要平仓, 原因)
        """
        try:
            if not self.is_trading_day():
                return False, ""
                
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            # 收盘前警告
            if current_time >= self.market_times['warning']:
                self.logger.warning(
                    f"接近收盘时间:\n"
                    f"  当前时间: {current_time}\n"
                    f"  强制平仓时间: {self.market_times['force_close']}"
                )
            
            # 强制平仓检查
            if current_time >= self.market_times['force_close']:
                self.logger.warning(
                    f"触发收盘平仓:\n"
                    f"  当前时间: {current_time}\n"
                    f"  收盘时间: {self.market_times['regular']['close']}"
                )
                return True, "收盘平仓"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查收盘平仓时间出错: {str(e)}")
            return False, ""

    def is_trading_time(self) -> bool:
        """检查是否在交易时间内"""
        try:
            # 测试模式下直接返回 True
            if self.test_mode:
                self.logger.info("测试模式: 忽略交易时间限制")
                return True
                
            current = datetime.now(self.tz)
            current_time = current.strftime('%H:%M')
            
            # 检查是否为交易日
            if not self.is_trading_day():
                self.logger.info("当前不是交易日")
                return False
            
            # 检查是否在交易时间内
            if (self.market_times['regular']['open'] <= current_time <= 
                self.market_times['regular']['close']):
                return True
                
            self.logger.info(f"当前不在交易时间 ({current_time})")
            return False
            
        except Exception as e:
            self.logger.error(f"检查交易时间出错: {str(e)}")
            return False

    def is_trading_day(self) -> bool:
        """检查是否为交易日"""
        try:
            current_date = datetime.now(self.tz).date()
            
            # 检查是否为周末
            if current_date.weekday() > 4:  # 5=周六, 6=周日
                return False
            
            # 检查是否为假期
            if current_date.strftime('%Y-%m-%d') in self.holidays:
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"检查交易日出错: {str(e)}")
            return False

    def get_next_market_open(self) -> datetime:
        """获取下一个交易日开盘时间"""
        try:
            current = datetime.now(self.tz)
            next_date = current.date()
            
            # 如果当前已过今日开盘时间，获取下一个交易日
            if current.strftime('%H:%M') >= self.market_times['regular']['open']:
                next_date += timedelta(days=1)
            
            # 找到下一个交易日
            while True:
                if (next_date.weekday() <= 4 and  # 非周末
                    next_date.strftime('%Y-%m-%d') not in self.holidays):  # 非假期
                    break
                next_date += timedelta(days=1)
            
            # 构建开盘时间
            open_time = datetime.strptime(
                self.market_times['regular']['open'],
                '%H:%M'
            ).time()
            
            next_open = datetime.combine(next_date, open_time)
            return self.tz.localize(next_open)
            
        except Exception as e:
            self.logger.error(f"获取下一个开市时间出错: {str(e)}")
            return datetime.now(self.tz) + timedelta(days=1)

    def get_session_status(self) -> Dict[str, bool]:
        """获取各交易时段状态"""
        try:
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            status = {}
            for session in ['pre_market', 'regular', 'post_market']:
                session_times = self.market_times.get(session)
                if session_times:
                    status[session] = (
                        session_times['open'] <= current_time <= session_times['close']
                    )
            
            return status
            
        except Exception as e:
            self.logger.error(f"获取交易时段状态出错: {str(e)}")
            return {}

    def log_time_status(self):
        """记录时间状态"""
        try:
            current = datetime.now(self.tz)
            
            self.logger.info(
                f"\n=== 时间状态 ===\n"
                f"当前时间: {current.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"交易日: {'是' if self.is_trading_day() else '否'}\n"
                f"交易时段: {self.get_session_status()}\n"
                f"{'='*20}"
            )
            
        except Exception as e:
            self.logger.error(f"记录时间状态时出错: {str(e)}")

    def check_expiry_close(self, symbol: str) -> Tuple[bool, str]:
        """检查是否需要提前平仓（针对次日到期的期权）"""
        try:
            # 测试模式下不强制平仓
            if self.test_mode:
                return False, ""
            
            # 检查是否为期权
            if not re.search(r'\d{6}[CP]\d+\.US$', symbol):
                return False, ""
            
            # 从期权代码中提取到期日
            expiry_date_str = re.search(r'(\d{6})[CP]', symbol).group(1)
            expiry_date = datetime.strptime(expiry_date_str, '%y%m%d').date()
            
            # 获取当前日期
            current_date = datetime.now(self.tz).date()
            
            # 如果是次日到期，且当前时间超过警告时间，则需要平仓
            if (expiry_date - current_date).days <= 1:
                current_time = datetime.now(self.tz).strftime('%H:%M')
                if current_time >= self.market_times['warning']:
                    self.logger.warning(
                        f"期权即将到期，需要提前平仓:\n"
                        f"  合约: {symbol}\n"
                        f"  到期日: {expiry_date}\n"
                        f"  当前时间: {current_time}"
                    )
                    return True, "期权次日到期提前平仓"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查期权到期平仓时出错: {str(e)}")
            return False, "" 