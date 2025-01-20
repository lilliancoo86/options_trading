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

class DataCleaner:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.tz = pytz.timezone('America/New_York')
        
        # 数据目录
        self.base_dir = Path(config['DATA_CONFIG']['base_dir'])
        self.market_data_dir = Path(config['DATA_CONFIG']['market_data_dir'])
        self.log_dir = Path(config['LOGGING_CONFIG']['file_path']).parent
        
        # 清理配置
        self.retention_days = config['DATA_CONFIG']['retention_days']
        self.max_log_size = config['LOGGING_CONFIG']['max_bytes']
        self.log_backup_count = config['LOGGING_CONFIG']['backup_count']
        self.compression = config['DATA_CONFIG']['compression']

    async def clean_old_data(self):
        """清理过期数据"""
        try:
            cutoff_date = datetime.now(self.tz) - timedelta(days=self.retention_days)
            
            # 清理K线数据
            kline_dir = self.market_data_dir / 'klines'
            if kline_dir.exists():
                for file in kline_dir.glob('*.csv'):
                    try:
                        df = pd.read_csv(file)
                        df['time'] = pd.to_datetime(df['time'])
                        # 只保留截止日期之后的数据
                        df = df[df['time'] > cutoff_date]
                        if not df.empty:
                            df.to_csv(file, index=False)
                            self.logger.info(f"已清理旧数据: {file.name}")
                    except Exception as e:
                        self.logger.error(f"清理K线数据出错 ({file.name}): {str(e)}")

            # 压缩旧数据
            if self.compression:
                await self._compress_old_data(cutoff_date)
                
        except Exception as e:
            self.logger.error(f"清理过期数据时出错: {str(e)}")

    async def _compress_old_data(self, cutoff_date: datetime):
        """压缩旧数据"""
        try:
            kline_dir = self.market_data_dir / 'klines'
            archive_dir = self.market_data_dir / 'archive'
            archive_dir.mkdir(exist_ok=True)
            
            for file in kline_dir.glob('*.csv'):
                try:
                    # 检查文件最后修改时间
                    mtime = datetime.fromtimestamp(file.stat().st_mtime, self.tz)
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
                    self.logger.error(f"压缩数据出错 ({file.name}): {str(e)}")
                    
        except Exception as e:
            self.logger.error(f"压缩旧数据时出错: {str(e)}")

    async def rotate_logs(self):
        """轮转日志文件"""
        try:
            log_file = Path(self.config['LOGGING_CONFIG']['file_path'])
            if not log_file.exists():
                return
                
            # 检查日志大小
            if log_file.stat().st_size > self.max_log_size:
                # 创建日志归档目录
                archive_dir = log_file.parent / 'archive'
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
                self.logger.info("已完成每日数据清理")
            else:
                # 其他时间只检查日志大小
                log_file = Path(self.config['LOGGING_CONFIG']['file_path'])
                if log_file.exists() and log_file.stat().st_size > self.max_log_size:
                    await self.rotate_logs()
                    
        except Exception as e:
            self.logger.error(f"执行清理任务时出错: {str(e)}") 