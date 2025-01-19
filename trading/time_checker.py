"""交易时间检查模块"""
from datetime import datetime, time, timedelta
import pytz
from typing import Dict, Any, Tuple
import logging

class TimeChecker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 市场时间设置
        self.market_times = {
            'open': '09:30',
            'close': '16:00',
            'force_close': '15:45',  # 收盘前15分钟强制平仓
            'warning': '15:40'       # 收盘前20分钟发出警告
        }

    def check_force_close(self) -> Tuple[bool, str]:
        """
        检查是否需要强制平仓
        Returns:
            Tuple[bool, str]: (是否需要平仓, 原因)
        """
        try:
            current_time = datetime.now(self.tz).strftime('%H:%M')
            
            # 收盘前警告
            if current_time >= self.market_times['warning']:
                self.logger.warning(f"接近收盘时间 ({current_time})")
            
            # 强制平仓检查
            if current_time >= self.market_times['force_close']:
                self.logger.warning(f"触发收盘平仓时间: {current_time}")
                return True, "收盘平仓"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查收盘平仓时间出错: {str(e)}")
            return False, ""

    def _parse_time(self, time_str: str) -> time:
        """解析时间字符串为time对象"""
        hour, minute, second = map(int, time_str.split(':'))
        return time(hour, minute, second)

    def current_time(self) -> datetime:
        """获取当前美东时间"""
        return datetime.now(self.tz)

    def current_time_str(self) -> str:
        """获取当前时间字符串 (HH:MM:SS)"""
        return self.current_time().strftime('%H:%M:%S')

    def get_next_market_open(self) -> datetime:
        """获取下一个交易日开盘时间"""
        current = self.current_time()
        current_date = current.date()
        
        # 创建今天的开盘时间
        today_open = datetime.combine(
            current_date,
            self._parse_time(self.market_times['open'])
        )
        today_open = self.tz.localize(today_open)
        
        # 如果当前时间已过今天开盘时间，返回明天的开盘时间
        if current.time() >= self._parse_time(self.market_times['open']):
            next_open = today_open + timedelta(days=1)
        else:
            next_open = today_open
        
        return next_open

    def is_trading_time(self) -> bool:
        """检查是否在交易时间内"""
        current_time = self.current_time().time()
        return self._parse_time(self.market_times['open']) <= current_time <= self._parse_time(self.market_times['close']) 