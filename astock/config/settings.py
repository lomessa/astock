"""全局配置管理，支持 .env 文件覆盖。"""
import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from dotenv import load_dotenv

# 加载 .env
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)


@dataclass
class DataConfig:
    """数据配置"""
    # 缓存目录
    cache_dir: str = field(default_factory=lambda: str(Path.home() / ".astock" / "cache"))
    # 本地 SQLite 数据库路径
    db_path: str = field(default_factory=lambda: str(Path.home() / ".astock" / "astock.db"))
    # 缓存有效期（秒）
    cache_ttl: int = 300
    # 历史 K 线天数
    history_days: int = 60
    # 请求重试次数
    retry_times: int = 3
    # 数据源优先级（从左到右）
    source_priority: List[str] = field(default_factory=lambda: ["tushare", "baostock", "yahoo", "akshare"])
    # 各数据源开关
    enable_akshare: bool = True
    enable_baostock: bool = True
    enable_tushare: bool = False
    enable_yahoo: bool = True
    # Tushare Pro token
    tushare_token: Optional[str] = None


@dataclass
class RiskConfig:
    """风控配置"""
    # 单票最大仓位（占总资金比例）
    max_position_per_stock: float = 0.20
    # 单账户最大持仓数量
    max_holding_count: int = 5
    # 单日最大亏损比例（相对总资金）
    max_daily_loss_ratio: float = 0.03
    # 单笔止损比例（相对买入价）
    stop_loss_ratio: float = 0.07
    # 单笔止盈比例（相对买入价）
    stop_profit_ratio: float = 0.15
    # 炸板/开板立即止盈比例（相对买入价）
    dynamic_stop_profit: float = 0.05
    # 禁止买入的市值上限（亿元）
    max_market_cap: float = 500.0
    # 禁止买入的市值下限（亿元）
    min_market_cap: float = 10.0
    # 黑名单
    blacklist: List[str] = field(default_factory=list)


@dataclass
class StrategyConfig:
    """策略配置"""
    # 首板策略开关
    enable_first_board: bool = True
    # 连板策略开关
    enable_continuous_board: bool = True
    # 炸板回封策略开关
    enable_reseal: bool = True
    # 竞价超预期策略开关
    enable_auction: bool = True
    # 板块资金共振策略开关
    enable_sector_flow: bool = True
    # 板块资金共振参数
    sector_flow_top_n: int = 20      # 取近5日板块净流入Top N
    # 爆发首日增强模式
    sector_flow_burst_mode: bool = True      # 是否只追新进入Top5的板块
    sector_flow_lookback_days: int = 3       # 看过去几天历史判断"新进入"
    sector_flow_new_entry_bonus: float = 0.15  # 新爆发板块额外加分（占满分比例）
    # 板块异动低吸策略开关
    enable_sector_flow_surge: bool = True
    # 板块异动低吸参数
    sector_flow_surge_top_n: int = 8   # 只关注最强势的前N个板块（减少请求量）

    # 首板筛选参数
    fb_max_float_mv: float = 200.0      # 流通市值上限（亿）
    fb_min_turnover: float = 3.0        # 最小换手率%
    fb_max_turnover: float = 30.0       # 最大换手率%
    fb_pre_max_change: float = 5.0      # 昨日最大涨幅%（排除昨日大涨）

    # 连板筛选参数
    cb_max_board: int = 5               # 最高连板数（超过不接力）
    cb_min_board: int = 2               # 最低连板数
    cb_min_amount: float = 1.0          # 最小封单金额（亿）

    # 炸板回封参数
    reseal_max_open_times: int = 3      # 最大开板次数
    reseal_min_reseal_time: str = "10:00"  # 最晚回封时间

    # 强势股回调参数
    enable_pullback: bool = True        # 策略开关
    pb_min_60d_change: float = 20.0     # 60日最小涨幅%（确认强势）
    pb_max_candidates: int = 150        # 深入分析的强势股数量上限
    pb_min_pullback: float = 15.0       # 最小回调幅度%
    pb_max_pullback: float = 30.0       # 最大回调幅度%
    pb_stop_loss: float = 0.08          # 止损比例（8%）
    pb_stop_profit: float = 0.15        # 止盈比例（15%）
    pb_dynamic_stop: float = 0.10       # 动态止盈（10%）
    pb_min_market_cap: float = 10.0     # 最小流通市值（亿）
    pb_max_market_cap: float = 500.0    # 最大流通市值（亿）
    pb_enable_industry_filter: bool = True  # 是否启用行业过滤


@dataclass
class NotifyConfig:
    """推送配置"""
    # 钉钉
    dingtalk_webhook: Optional[str] = None
    dingtalk_secret: Optional[str] = None
    # 飞书
    feishu_webhook: Optional[str] = None
    # 企业微信
    wechat_webhook: Optional[str] = None
    # 推送开关
    enable_push: bool = True
    # 只推送给定置信度以上的信号
    min_confidence: float = 0.6


@dataclass
class Settings:
    """全局配置"""
    data: DataConfig = field(default_factory=DataConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)

    @classmethod
    def from_env(cls) -> "Settings":
        """从环境变量读取配置"""
        s = cls()
        # 数据
        s.data.cache_dir = os.getenv("ASTOCK_CACHE_DIR", s.data.cache_dir)
        s.data.db_path = os.getenv("ASTOCK_DB_PATH", s.data.db_path)
        s.data.cache_ttl = int(os.getenv("ASTOCK_CACHE_TTL", str(s.data.cache_ttl)))
        s.data.history_days = int(os.getenv("ASTOCK_HISTORY_DAYS", str(s.data.history_days)))
        priority = os.getenv("ASTOCK_SOURCE_PRIORITY")
        if priority:
            s.data.source_priority = [p.strip() for p in priority.split(",")]
        s.data.enable_akshare = os.getenv("ASTOCK_ENABLE_AKSHARE", "true").lower() == "true"
        s.data.enable_baostock = os.getenv("ASTOCK_ENABLE_BAOSTOCK", "true").lower() == "true"
        s.data.enable_tushare = os.getenv("ASTOCK_ENABLE_TUSHARE", "false").lower() == "true"
        s.data.enable_yahoo = os.getenv("ASTOCK_ENABLE_YAHOO", "true").lower() == "true"
        s.data.tushare_token = os.getenv("TUSHARE_TOKEN") or s.data.tushare_token

        # 风控
        s.risk.max_position_per_stock = float(os.getenv("ASTOCK_MAX_POS", str(s.risk.max_position_per_stock)))
        s.risk.stop_loss_ratio = float(os.getenv("ASTOCK_STOP_LOSS", str(s.risk.stop_loss_ratio)))
        s.risk.stop_profit_ratio = float(os.getenv("ASTOCK_STOP_PROFIT", str(s.risk.stop_profit_ratio)))

        # 策略
        s.strategy.enable_first_board = os.getenv("ASTOCK_FB", "true").lower() == "true"
        s.strategy.enable_continuous_board = os.getenv("ASTOCK_CB", "true").lower() == "true"
        s.strategy.enable_pullback = os.getenv("ASTOCK_PB", "true").lower() == "true"
        s.strategy.enable_sector_flow = os.getenv("ASTOCK_SECTOR_FLOW", "true").lower() == "true"
        s.strategy.enable_sector_flow_surge = os.getenv("ASTOCK_SECTOR_FLOW_SURGE", "true").lower() == "true"

        # 推送
        s.notify.dingtalk_webhook = os.getenv("DINGTALK_WEBHOOK") or s.notify.dingtalk_webhook
        s.notify.dingtalk_secret = os.getenv("DINGTALK_SECRET") or s.notify.dingtalk_secret
        s.notify.feishu_webhook = os.getenv("FEISHU_WEBHOOK") or s.notify.feishu_webhook
        s.notify.wechat_webhook = os.getenv("WECHAT_WEBHOOK") or s.notify.wechat_webhook
        s.notify.enable_push = os.getenv("ASTOCK_PUSH", "true").lower() == "true"

        return s


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局单例配置"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings
