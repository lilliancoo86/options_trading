"""
数据清理模块
负责管理日志和历史数据的大小、备份和清理
"""
from typing import Dict, Any
import logging
import os
from pathlib import Path
from datetime import datetime, timedelta
import pytz
import gzip
import shutil
import pandas as pd
import asyncio
import time
from longport.openapi import (
    Config,
    QuoteContext,
    SubType,
    Period,
    AdjustType
)

class DataCleaner:
    def __init__(self, config: Dict[str, Any]):
        """初始化数据清理器"""
        self.config = config
        self.logger = logging.getLogger(__name__)
        
        # 从配置文件读取清理配置
        self.cleanup_config = config.get('CLEANUP_CONFIG', {})
        self.data_config = config.get('DATA_CONFIG', {})
        
        # 设置数据目录
        self.base_dir = Path(self.data_config.get('base_dir', '/home/options_trading/data'))
        self.market_data_dir = Path(self.data_config.get('market_data_dir', self.base_dir / 'market_data'))
        self.options_data_dir = Path(self.data_config.get('options_data_dir', self.base_dir / 'options_data'))
        
        # 日志配置 - 修正从配置中获取路径
        self.logging_config = config.get('LOGGING_CONFIG', {})
        self.log_dir = Path(self.logging_config.get('file_path', 'logs/trading.log')).parent
        self.max_log_size = self.logging_config.get('max_bytes', 10 * 1024 * 1024)
        self.log_backup_count = self.logging_config.get('backup_count', 30)
        
        # 数据保留配置 - 添加默认值
        self.retention_config = {
            'klines': self.cleanup_config.get('klines_retention_days', 365),
            'options': self.cleanup_config.get('options_retention_days', 30),
            'logs': self.cleanup_config.get('logs_retention_days', 30),
            'market_data': self.cleanup_config.get('market_data_retention_days', 90)
        }
        
        # 清理任务配置 - 确保使用正确的配置路径
        self.cleanup_interval = int(self.cleanup_config.get('cleanup_interval', 86400))  # 默认24小时
        self.cleanup_time = self.cleanup_config.get('cleanup_time', '00:00')
        self.cleanup_enabled = self.cleanup_config.get('cleanup_enabled', True)
        
        # 清理规则 - 使用 get 方法获取子配置
        cleanup_rules = self.cleanup_config.get('cleanup_rules', {})
        self.cleanup_rules = {
            'min_records': cleanup_rules.get('min_records', 100),
            'max_file_age': cleanup_rules.get('max_file_age', 365),
            'delete_empty_dirs': cleanup_rules.get('delete_empty_dirs', True),
            'skip_recent_files': cleanup_rules.get('skip_recent_files', True),
            'recent_threshold': cleanup_rules.get('recent_threshold', 3600)
        }
        
        # 日志清理配置 - 使用 get 方法获取子配置
        log_cleanup = self.cleanup_config.get('log_cleanup', {})
        self.log_cleanup = {
            'max_log_size': log_cleanup.get('max_log_size', 10 * 1024 * 1024),
            'max_log_files': log_cleanup.get('max_log_files', 30),
            'compress_logs': log_cleanup.get('compress_logs', False),
            'delete_empty_logs': log_cleanup.get('delete_empty_logs', True)
        }
        
        # 压缩配置
        self.compression = self.data_config.get('compression', True)
        
        # 添加时区配置
        self.tz = pytz.timezone('America/New_York')
        
        # 连接管理
        self._quote_ctx_lock = asyncio.Lock()
        self._quote_ctx = None
        self._last_quote_time = 0
        self._quote_timeout = 60  # 60秒超时
        
        # 请求限制
        self.request_times = []
        self.request_limit = config.get('request_limit', {
            'max_requests': 120,  # 每分钟最大请求数
            'time_window': 60     # 时间窗口（秒）
        })

    async def async_init(self):
        """异步初始化"""
        try:
            # 确保目录存在
            self.market_data_dir.mkdir(parents=True, exist_ok=True)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            
            self.logger.info("数据清理器初始化完成")
            return self
        except Exception as e:
            self.logger.error(f"数据清理器初始化失败: {str(e)}")
            raise

    async def clean_old_data(self):
        """清理过期数据"""
        try:
            # 确保 cutoff_date 是 UTC 时间
            cutoff_date = pd.Timestamp(datetime.now(self.tz) - timedelta(days=self.retention_config['klines'])).tz_localize(None)
            
            # 清理K线数据
            kline_dir = self.market_data_dir / 'klines'
            if kline_dir.exists():
                for file in kline_dir.glob('*.csv'):
                    try:
                        # 读取数据
                        df = pd.read_csv(file)
                        if df.empty:
                            self.logger.warning(f"文件为空: {file.name}")
                            continue
                        
                        # 检查数据条数
                        if len(df) <= 1000:
                            self.logger.debug(f"数据条数未超过1000，无需清理: {file.name}")
                            continue
                            
                        # 将时间列转换为 pandas datetime，并移除时区信息以进行比较
                        df['time'] = pd.to_datetime(df['time']).dt.tz_localize(None)
                        
                        # 保留最新的1000条数据
                        df_filtered = df.sort_values('time', ascending=False).head(1000)
                        
                        if len(df_filtered) > 0:
                            # 重新排序并保存
                            df_filtered = df_filtered.sort_values('time')
                            # 保存前重新格式化时间
                            df_filtered.loc[:, 'time'] = pd.to_datetime(df_filtered['time'])
                            df_filtered.to_csv(file, index=False)
                            self.logger.info(f"已清理旧数据: {file.name}, 保留最新 {len(df_filtered)} 条记录")
                        else:
                            self.logger.warning(f"清理后数据为空: {file.name}")
                        
                    except Exception as e:
                        self.logger.error(f"清理K线数据出错 ({file.name}): {str(e)}", exc_info=True)

            # 压缩旧数据
            if self.compression:
                await self._compress_old_data(cutoff_date)
                
        except Exception as e:
            self.logger.error(f"清理过期数据时出错: {str(e)}", exc_info=True)

    async def _compress_old_data(self, cutoff_date: pd.Timestamp):
        """压缩旧数据"""
        try:
            kline_dir = self.market_data_dir / 'klines'
            archive_dir = self.market_data_dir / 'archive'
            archive_dir.mkdir(exist_ok=True)
            
            for file in kline_dir.glob('*.csv'):
                try:
                    # 检查文件最后修改时间，转换为无时区的 Timestamp
                    mtime = pd.Timestamp(datetime.fromtimestamp(file.stat().st_mtime, self.tz)).tz_localize(None)
                    
                    if mtime < cutoff_date:
                        # 压缩文件
                        archive_path = archive_dir / f"{file.stem}_{mtime.strftime('%Y%m')}.gz"
                        with open(file, 'rb') as f_in:
                            with gzip.open(archive_path, 'wb') as f_out:
                                shutil.copyfileobj(f_in, f_out)
                        # 删除原文件
                        file.unlink()
                        self.logger.info(f"已压缩旧数据: {file.name} -> {archive_path.name}")
                except Exception as e:
                    self.logger.error(f"压缩数据出错 ({file.name}): {str(e)}", exc_info=True)
                    
        except Exception as e:
            self.logger.error(f"压缩旧数据时出错: {str(e)}", exc_info=True)

    async def rotate_logs(self):
        """轮转日志文件"""
        try:
            log_file = Path(self.log_dir / 'trading.log')
            if not log_file.exists():
                return
                
            # 检查日志大小
            if log_file.stat().st_size > self.max_log_size:
                # 创建日志归档目录
                archive_dir = self.log_dir / 'archive'
                archive_dir.mkdir(exist_ok=True)
                
                # 获取当前时间戳
                timestamp = datetime.now(self.tz).strftime('%Y%m%d_%H%M%S')
                
                # 压缩旧日志
                archive_path = archive_dir / f"trading_{timestamp}.log.gz"
                with open(log_file, 'rb') as f_in:
                    with gzip.open(archive_path, 'wb') as f_out:
                        shutil.copyfileobj(f_in, f_out)
                
                # 清空当前日志文件
                log_file.write_text('')
                
                # 清理30天前的日志
                self._cleanup_old_logs(archive_dir)
                
                self.logger.info(f"已归档日志文件: {archive_path.name}")
                
        except Exception as e:
            self.logger.error(f"轮转日志文件时出错: {str(e)}")

    def _cleanup_old_logs(self, archive_dir: Path):
        """清理30天前的日志"""
        try:
            # 保留最近30天的日志
            cutoff_date = datetime.now(self.tz) - timedelta(days=30)
            
            # 删除旧日志
            for log_file in archive_dir.glob('*.log.gz'):
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime, self.tz)
                if mtime < cutoff_date:
                    log_file.unlink()
                    self.logger.info(f"已删除30天前的日志: {log_file.name}")
                    
        except Exception as e:
            self.logger.error(f"清理旧日志时出错: {str(e)}")

    async def cleanup(self):
        """执行所有清理任务"""
        try:
            # 检查是否需要清理
            now = datetime.now(self.tz)
            
            # 每天凌晨2点执行完整清理
            if now.hour == 2:
                await self.clean_old_data()
                await self.rotate_logs()
                await self.cleanup_expired_options()
                self.logger.info("已完成每日数据清理")
            else:
                # 其他时间只检查日志大小
                log_file = Path(self.log_dir / 'trading.log')
                if log_file.exists() and log_file.stat().st_size > self.max_log_size:
                    await self.rotate_logs()
                    
        except Exception as e:
            self.logger.error(f"执行清理任务时出错: {str(e)}")

    async def _get_quote_ctx(self):
        """获取行情连接（带连接管理）"""
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
                
                # 创建新连接
                self._quote_ctx = QuoteContext(self.longport_config)
                self._last_quote_time = current_time
            
            return self._quote_ctx

    async def check_rate_limit(self):
        """检查请求限制"""
        try:
            current_time = time.time()
            # 清理过期的请求记录
            self.request_times = [t for t in self.request_times 
                                if current_time - t < self.request_limit['time_window']]
            
            # 检查是否超过限制
            if len(self.request_times) >= self.request_limit['max_requests']:
                wait_time = self.request_times[0] + self.request_limit['time_window'] - current_time
                if wait_time > 0:
                    self.logger.warning(f"达到请求限制，等待 {wait_time:.1f} 秒")
                    await asyncio.sleep(wait_time)
            
            # 记录新的请求时间
            self.request_times.append(current_time)
            
        except Exception as e:
            self.logger.error(f"检查请求限制时出错: {str(e)}")

    async def close(self):
        """关闭数据清理器"""
        try:
            if self._quote_ctx:
                try:
                    await self._quote_ctx.unsubscribe_all()
                    await self._quote_ctx.close()
                except Exception as e:
                    self.logger.warning(f"关闭行情连接时出错: {str(e)}")
                finally:
                    self._quote_ctx = None
            
            self.logger.info("数据清理器已关闭")
        except Exception as e:
            self.logger.error(f"关闭数据清理器时出错: {str(e)}")

    async def cleanup_expired_options(self):
        """清理已过期的期权数据"""
        try:
            self.logger.info("开始清理过期期权数据...")
            current_date = datetime.now(self.tz).date()
            files_cleaned = 0
            space_saved = 0  # 字节数
            
            # 遍历期权数据目录
            for file_path in self.options_data_dir.glob('*'):
                try:
                    if not file_path.is_file():
                        continue
                        
                    # 解析文件名中的期权信息
                    file_name = file_path.stem  # 不包含扩展名的文件名
                    
                    # 如果是期权链或希腊字母数据文件
                    if '_chain.csv' in file_path.name or '_greeks.csv' in file_path.name:
                        # 读取文件的第一行获取期权到期日
                        try:
                            df = pd.read_csv(file_path, nrows=1)
                            if 'expiry_date' in df.columns:
                                expiry_date = pd.to_datetime(df['expiry_date'].iloc[0]).date()
                                
                                # 如果期权已过期，移动到历史目录
                                if expiry_date < current_date:
                                    file_size = file_path.stat().st_size
                                    historical_path = self.historical_dir / 'options' / file_path.name
                                    historical_path.parent.mkdir(parents=True, exist_ok=True)
                                    
                                    # 移动文件到历史目录
                                    file_path.rename(historical_path)
                                    files_cleaned += 1
                                    space_saved += file_size
                                    self.logger.debug(f"已移动过期文件到历史目录: {file_path.name}")
                        except pd.errors.EmptyDataError:
                            # 如果文件为空，直接删除
                            file_size = file_path.stat().st_size
                            file_path.unlink()
                            files_cleaned += 1
                            space_saved += file_size
                            
                    # 清理隐含波动率历史数据
                    elif '_iv_history.csv' in file_path.name:
                        try:
                            df = pd.read_csv(file_path)
                            if not df.empty:
                                # 只保留指定天数的数据
                                df['date'] = pd.to_datetime(df['date'])
                                cutoff_date = current_date - timedelta(days=self.options_config['iv_history_days'])
                                df_filtered = df[df['date'] >= cutoff_date]
                                
                                if len(df_filtered) < len(df):
                                    original_size = file_path.stat().st_size
                                    df_filtered.to_csv(file_path, index=False)
                                    new_size = file_path.stat().st_size
                                    space_saved += (original_size - new_size)
                                    files_cleaned += 1
                        except pd.errors.EmptyDataError:
                            file_path.unlink()
                            files_cleaned += 1
                
                except Exception as e:
                    self.logger.error(f"清理文件时出错 {file_path.name}: {str(e)}")
                    continue
            
            # 清理历史目录中的旧数据
            await self._cleanup_historical_options()
            
            # 记录清理结果
            space_saved_mb = space_saved / (1024 * 1024)  # 转换为MB
            self.logger.info(
                f"期权数据清理完成:\n"
                f"  清理文件数: {files_cleaned}\n"
                f"  节省空间: {space_saved_mb:.2f}MB"
            )
            
        except Exception as e:
            self.logger.error(f"清理过期期权数据时出错: {str(e)}")

    async def _cleanup_historical_options(self):
        """清理历史期权数据"""
        try:
            current_date = datetime.now(self.tz).date()
            historical_options_dir = self.historical_dir / 'options'
            
            if not historical_options_dir.exists():
                return
                
            files_cleaned = 0
            space_saved = 0
            
            # 遍历历史目录
            for file_path in historical_options_dir.glob('*'):
                try:
                    if not file_path.is_file():
                        continue
                        
                    # 获取文件修改时间
                    file_time = datetime.fromtimestamp(file_path.stat().st_mtime).date()
                    
                    # 如果文件超过保留期限，删除
                    if (current_date - file_time).days > self.retention_config['options']:
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        files_cleaned += 1
                        space_saved += file_size
                        
                except Exception as e:
                    self.logger.error(f"清理历史文件时出错 {file_path.name}: {str(e)}")
                    continue
            
            if files_cleaned > 0:
                space_saved_mb = space_saved / (1024 * 1024)
                self.logger.info(
                    f"历史期权数据清理完成:\n"
                    f"  清理文件数: {files_cleaned}\n"
                    f"  节省空间: {space_saved_mb:.2f}MB"
                )
                
        except Exception as e:
            self.logger.error(f"清理历史期权数据时出错: {str(e)}")

    async def schedule_cleanup(self):
        """定期清理数据"""
        try:
            while True:
                # 每天凌晨2点运行清理
                now = datetime.now(self.tz)
                next_run = now.replace(hour=2, minute=0, second=0, microsecond=0)
                if now >= next_run:
                    next_run = next_run + timedelta(days=1)
                
                # 等待到下次运行时间
                wait_seconds = (next_run - now).total_seconds()
                await asyncio.sleep(wait_seconds)
                
                # 运行清理
                await self.cleanup_expired_options()
                
        except Exception as e:
            self.logger.error(f"调度清理任务时出错: {str(e)}")

    async def cleanup_klines(self):
        """清理K线数据"""
        try:
            # 使用配置中的保留天数
            cutoff_time = datetime.now(self.tz) - timedelta(
                days=self.retention_config['klines']
            )
            cutoff_timestamp = cutoff_time.timestamp()
            
            kline_dir = self.market_data_dir / 'klines'
            if not kline_dir.exists():
                return
                
            for file in kline_dir.glob('*.csv'):
                try:
                    if file.stat().st_mtime < cutoff_timestamp:
                        # 直接删除过期数据
                        file.unlink()
                        self.logger.info(f"已删除过期K线数据: {file.name}")
                            
                except Exception as e:
                    self.logger.error(f"处理K线文件出错 ({file}): {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"清理K线数据时出错: {str(e)}")

    async def cleanup_options_data(self):
        """清理期权数据"""
        try:
            cutoff_time = datetime.now(self.tz) - timedelta(
                days=self.retention_config['options']
            )
            cutoff_timestamp = cutoff_time.timestamp()
            
            if not self.options_data_dir.exists():
                return
                
            for file in self.options_data_dir.glob('**/*'):
                if not file.is_file():
                    continue
                    
                try:
                    if file.stat().st_mtime < cutoff_timestamp:
                        # 直接删除过期数据
                        file.unlink()
                        self.logger.info(f"已删除过期期权数据: {file.name}")
                            
                except Exception as e:
                    self.logger.error(f"处理期权文件出错 ({file}): {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"清理期权数据时出错: {str(e)}")

    async def cleanup_logs(self):
        """清理过期日志文件"""
        try:
            cutoff_time = time.time() - (
                self.retention_config['logs'] * 86400
            )
            
            for file in self.log_dir.glob('*.log*'):
                try:
                    if file.stat().st_mtime < cutoff_time:
                        # 直接删除过期日志
                        file.unlink()
                        self.logger.info(f"已删除过期日志: {file.name}")
                        
                except Exception as e:
                    self.logger.error(f"删除日志文件出错 ({file}): {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"清理日志文件时出错: {str(e)}")

    async def start_cleanup_task(self):
        """启动定期清理任务"""
        while True:
            try:
                self.logger.info("开始数据清理任务...")
                
                # 执行各类数据清理
                await self.cleanup_klines()
                await self.cleanup_options_data()
                await self.cleanup_logs()
                
                self.logger.info("数据清理任务完成")
                
                # 等待下次清理
                await asyncio.sleep(self.cleanup_interval)
                
            except Exception as e:
                self.logger.error(f"数据清理任务出错: {str(e)}")
                await asyncio.sleep(3600)  # 出错后等待1小时再试 