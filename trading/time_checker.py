"""交易时间检查模块"""
from datetime import datetime, time, timedelta
import pytz

class TimeChecker:
    def __init__(self, market_open: str, market_close: str, force_close_time: str):
        """
        初始化时间检查器
        
        Args:
            market_open: 市场开盘时间 (HH:MM:SS)
            market_close: 市场收盘时间 (HH:MM:SS)
            force_close_time: 强制平仓时间 (HH:MM:SS)
        """
        self.market_open = self._parse_time(market_open)
        self.market_close = self._parse_time(market_close)
        self.force_close_time = self._parse_time(force_close_time)
        self.tz = pytz.timezone('America/New_York')

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
            self.market_open
        )
        today_open = self.tz.localize(today_open)
        
        # 如果当前时间已过今天开盘时间，返回明天的开盘时间
        if current.time() >= self.market_open:
            next_open = today_open + timedelta(days=1)
        else:
            next_open = today_open
        
        return next_open

    def is_trading_time(self) -> bool:
        """检查是否在交易时间内"""
        current_time = self.current_time().time()
        return self.market_open <= current_time <= self.market_close

    def should_force_close(self) -> bool:
        """检查是否需要强制平仓"""
        current_time = self.current_time().time()
        return current_time >= self.force_close_time 