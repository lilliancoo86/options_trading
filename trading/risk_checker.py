"""
风险检查模块
负责检查持仓风险和市场风险
"""
from typing import Dict, Any, Tuple, List
import logging
from datetime import datetime
import pytz
import re

class RiskChecker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 风险控制参数
        self.risk_limits = {
            'volatility': {
                'max_daily': 5.0,  # 最大日内波动率
                'warning': 3.0     # 波动率警告线
            },
            'vix': {
                'max_level': 35.0,
                'warning': 25.0
            },
            'option': {
                'stop_loss': -10.0,  # 期权固定10%止损
                'take_profit': None  # 期权不设固定止盈
            },
            'stock': {
                'stop_loss': -3.0,   # 股票固定3%止损
                'take_profit': 5.0    # 股票固定5%止盈
            },
            'market': {
                'max_position_value': 100000,  # 单个持仓限额
                'max_total_exposure': 500000,  # 总持仓限额
                'max_positions': 10  # 最大持仓数量限制
            }
        }
        
        # 添加收盘平仓设置
        self.market_close = {
            'force_close_time': '15:45',  # 收盘前15分钟强制平仓
            'warning_time': '15:40'       # 收盘前20分钟发出警告
        }

        # 添加移动止损配置
        self.trailing_stop = {
            'activation': 15.0,     # 盈利15%时激活移动止损
            'trail_percent': 5.0,   # 回撤5%时触发
            'min_profit': 10.0      # 确保至少保住10%的利润
        }

        # ATR 配置
        self.atr_config = {
            'period': 14,          # ATR周期
            'multiplier': 2.0,     # ATR倍数
            'min_periods': 1,      # 最小所需周期数
            'intraday': {
                'interval': '1m',  # 分时ATR间隔
                'high_threshold': 2.0,  # 高点ATR倍数
                'low_threshold': 0.5,   # 低点ATR倍数
            }
        }
        
        # 分批止盈配置
        self.take_profit_stages = {
            'stage1': {
                'threshold': 25.0,  # 25%时止盈
                'ratio': 0.33      # 卖出1/3仓位
            },
            'stage2': {
                'threshold': 40.0,  # 40%时止盈
                'ratio': 0.5       # 卖出一半剩余仓位
            }
        }
        
        # 缓存数据
        self._atr_cache = {
            'time': None,
            'data': {}
        }
        self._position_peaks = {}
        self._intraday_data = {}

        # 初始化时添加 TimeChecker 引用
        self.time_checker = config.get('time_checker')
        
        # 添加仓位阶段跟踪
        self._position_stages = {}  # 用于跟踪分批止盈阶段

        # 重新排序风险检查优先级
        self.risk_priority = {
            'stop_loss': 1,     # 固定止损永远最高优先级（保护本金）
            'trailing': 2,      # 移动止损次高优先级（保护利润）
            'time': 3,          # 收盘时间风险（避免隔夜风险）
            'expiry': 4,        # 期权到期风险
            'atr': 5,          # ATR动态调整（日内波动管理）
            'take_profit': 6,   # 常规止盈最低优先级
        }

    async def calculate_atr(self, symbol: str, klines: List[Dict]) -> float:
        """计算ATR"""
        try:
            current_time = datetime.now(self.tz)
            
            # 检查缓存是否有效（1分钟内）
            if (self._atr_cache['time'] and 
                (current_time - self._atr_cache['time']).total_seconds() < 60 and
                symbol in self._atr_cache['data']):
                return self._atr_cache['data'][symbol]
            
            if len(klines) < self.atr_config['min_periods']:
                return 0.0
                
            tr_list = []
            for i in range(1, len(klines)):
                high = float(klines[i]['high'])
                low = float(klines[i]['low'])
                prev_close = float(klines[i-1]['close'])
                
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                tr_list.append(tr)
            
            # 计算ATR
            atr = sum(tr_list[-self.atr_config['period']:]) / len(tr_list[-self.atr_config['period']:])
            
            # 更新缓存
            self._atr_cache['time'] = current_time
            self._atr_cache['data'][symbol] = atr
            
            return atr
            
        except Exception as e:
            self.logger.error(f"计算ATR时出错: {str(e)}")
            return 0.0

    async def check_intraday_position(self, position: Dict[str, Any], 
                                    market_data: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查日内持仓"""
        try:
            symbol = position['symbol']
            current_price = float(position['current_price'])
            
            # 获取1分钟K线数据
            klines = await self.quote_ctx.get_candlestick(
                symbol=symbol,
                period="1m",
                count=self.atr_config['period']
            )
            
            if not klines:
                return False, "", 0.0
                
            # 计算ATR
            atr = await self.calculate_atr(symbol, klines)
            if atr == 0:
                return False, "", 0.0
                
            # 获取当前分钟的高低点
            current_high = float(klines[-1]['high'])
            current_low = float(klines[-1]['low'])
            
            # 检查是否触及高点ATR
            if current_price >= current_high + (atr * self.atr_config['intraday']['high_threshold']):
                return True, "ATR高点止盈", 1.0  # 全部止盈
                
            # 检查是否触及低点ATR且有足够利润
            if (current_price <= current_low - (atr * self.atr_config['intraday']['low_threshold']) and
                self._has_sufficient_profit(position)):
                return True, "ATR低点建仓", 0.25  # 建仓1/4仓位
                
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查日内持仓时出错: {str(e)}")
            return False, "", 0.0

    def _has_sufficient_profit(self, position: Dict[str, Any]) -> bool:
        """检查是否有足够利润"""
        try:
            cost_price = float(position['cost_price'])
            current_price = float(position['current_price'])
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            return pnl_pct >= self.trailing_stop['min_profit']
            
        except Exception as e:
            self.logger.error(f"检查利润时出错: {str(e)}")
            return False

    async def check_position_risk(self, position: Dict[str, Any], market_data: Dict[str, Any]) -> Tuple[bool, str, float]:
        """检查持仓风险"""
        try:
            symbol = position['symbol']
            current_price = float(position['current_price'])
            cost_price = float(position['cost_price'])
            pnl_pct = (current_price - cost_price) / cost_price * 100
            
            # 判断是否为期权
            is_option = bool(re.search(r'\d{6}[CP]\d+\.US$', symbol))
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            # 1. 固定止损（保护本金，最高优先级）
            if limits['stop_loss'] is not None and pnl_pct <= limits['stop_loss']:
                self.logger.warning(
                    f"触发止损信号:\n"
                    f"  标的: {symbol}\n"
                    f"  类型: {'期权' if is_option else '股票'}\n"
                    f"  当前亏损: {pnl_pct:.1f}%\n"
                    f"  止损线: {limits['stop_loss']}%"
                )
                return True, "止损", 1.0
            
            # 2. 移动止损（保护已有利润）
            if is_option and pnl_pct >= self.trailing_stop['activation']:
                if symbol not in self._position_peaks:
                    self._position_peaks[symbol] = pnl_pct
                else:
                    self._position_peaks[symbol] = max(self._position_peaks[symbol], pnl_pct)
                
                peak_pnl = self._position_peaks[symbol]
                drawdown = peak_pnl - pnl_pct
                
                if (drawdown >= self.trailing_stop['trail_percent'] and 
                    pnl_pct >= self.trailing_stop['min_profit']):
                    self.logger.warning(
                        f"触发移动止损:\n"
                        f"  标的: {symbol}\n"
                        f"  最高收益: {peak_pnl:.1f}%\n"
                        f"  当前收益: {pnl_pct:.1f}%\n"
                        f"  回撤幅度: {drawdown:.1f}%"
                    )
                    return True, "移动止损", 1.0
            
            # 3. 收盘时间风险（避免隔夜风险）
            need_close, reason = self.time_checker.check_force_close()
            if need_close:
                return True, reason, 1.0
            
            # 4. 期权到期风险
            need_close, reason = self.time_checker.check_expiry_close(symbol)
            if need_close:
                return True, reason, 1.0
            
            # 5. ATR动态调整（日内波动管理）
            need_close, reason, ratio = await self.check_intraday_position(position, market_data)
            if need_close:
                return True, reason, ratio
            
            # 6. 分批止盈（最低优先级）
            if is_option and pnl_pct >= self.take_profit_stages['stage2']['threshold']:
                if symbol not in self._position_stages:
                    self._position_stages[symbol] = set()
                if 'stage2' not in self._position_stages[symbol]:
                    self._position_stages[symbol].add('stage2')
                    return True, "分批止盈-阶段2", self.take_profit_stages['stage2']['ratio']
                    
            elif is_option and pnl_pct >= self.take_profit_stages['stage1']['threshold']:
                if symbol not in self._position_stages:
                    self._position_stages[symbol] = set()
                if 'stage1' not in self._position_stages[symbol]:
                    self._position_stages[symbol].add('stage1')
                    return True, "分批止盈-阶段1", self.take_profit_stages['stage1']['ratio']
            
            return False, "", 0.0
            
        except Exception as e:
            self.logger.error(f"检查持仓风险时出错: {str(e)}")
            return False, "", 0.0

    async def check_market_risk(self, vix_level: float, daily_volatility: float) -> Tuple[bool, str]:
        """
        检查市场风险
        
        Returns:
            Tuple[bool, str]: (是否风险过高, 原因)
        """
        try:
            vol_limits = self.risk_limits['volatility']
            
            # 检查日内波动率
            if daily_volatility > vol_limits['max_daily']:
                self.logger.warning(
                    f"日内波动率过高:\n"
                    f"  当前波动率: {daily_volatility:.1f}%\n"
                    f"  最大限制: {vol_limits['max_daily']}%"
                )
                return True, "波动率过高"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查市场风险时出错: {str(e)}")
            self.logger.exception("详细错误信息:")
            return False, ""

    def log_risk_status(self, position: Dict[str, Any]):
        """记录风险状态"""
        try:
            current_price = float(position.get('current_price', 0))
            cost_price = float(position.get('cost_price', 0))
            if cost_price == 0:
                return
            
            pnl_pct = (current_price - cost_price) / cost_price * 100
            is_option = self._is_option(position['symbol'])
            limits = self.risk_limits['option'] if is_option else self.risk_limits['stock']
            
            self.logger.info(
                f"\n=== 风险状态 [{position['symbol']}] ===\n"
                f"当前价格: ${current_price:.2f}\n"
                f"成本价格: ${cost_price:.2f}\n"
                f"收益率: {pnl_pct:+.1f}%\n"
                f"止损线: {limits['stop_loss']}%\n"
                f"止盈线: {limits['take_profit'] if not is_option else 'N/A'}\n"
                f"当前时间: {datetime.now(self.tz).strftime('%H:%M:%S')}\n"
                f"{'='*40}"
            )
            
        except Exception as e:
            self.logger.error(f"记录风险状态时出错: {str(e)}")

    def _is_option(self, symbol: str) -> bool:
        """检查是否为期权"""
        return any(x in symbol for x in ['C', 'P'])

    def check_new_position_risk(self, symbol: str, price: float, volume: int) -> Tuple[bool, str]:
        """检查新开仓位的风险"""
        try:
            # 计算持仓价值
            position_value = price * volume
            
            # 检查单个持仓限额
            if position_value > self.risk_limits['market']['max_position_value']:
                self.logger.warning(
                    f"超过单个持仓限额:\n"
                    f"  标的: {symbol}\n"
                    f"  持仓价值: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['max_position_value']}"
                )
                return True, "超过持仓限额"
            
            # 检查总持仓限额
            total_value = self.risk_stats['total_exposure'] + position_value
            if total_value > self.risk_limits['market']['max_total_exposure']:
                self.logger.warning(
                    f"超过总持仓限额:\n"
                    f"  当前总持仓: ${self.risk_stats['total_exposure']:.2f}\n"
                    f"  新增持仓: ${position_value:.2f}\n"
                    f"  限额: ${self.risk_limits['market']['max_total_exposure']}"
                )
                return True, "超过总持仓限额"
            
            # 检查持仓数量限制
            if self.risk_stats['total_positions'] >= self.risk_limits['market']['max_positions']:
                self.logger.warning(f"超过最大持仓数量限制: {self.risk_stats['total_positions']}")
                return True, "超过持仓数量限制"
            
            return False, ""
            
        except Exception as e:
            self.logger.error(f"检查新开仓位风险时出错: {str(e)}")
            return False, ""