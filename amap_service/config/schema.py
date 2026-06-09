from typing import Literal, Optional

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, Field, field_validator, model_validator


def _validate_cron(value: str) -> str:
    # Raises ValueError on malformed expression → pydantic surfaces as ValidationError.
    CronTrigger.from_crontab(value)
    return value


def _split_csv(value: Optional[str]):
    """Comma-separated string -> set of trimmed non-empty values; None when unset/blank."""
    if not value:
        return None
    items = {part.strip() for part in value.split(",") if part.strip()}
    return items or None


class AuthConfig(BaseModel):
    type: Literal["none", "header"] = "none"
    headers: dict[str, str] = Field(default_factory=dict)


class JobConfig(BaseModel):
    path: str
    cron: str
    enabled: bool = True
    parse_mode: Literal["memory", "stream"] = "memory"

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        return _validate_cron(v)


class AmapJobs(BaseModel):
    road_network: JobConfig
    traffic_status: JobConfig


class AmapConfig(BaseModel):
    endpoint: str
    auth: AuthConfig = Field(default_factory=AuthConfig)
    jobs: AmapJobs


class TransitConfig(BaseModel):
    enabled: bool = True
    cron: str = "0 3 * * *"
    username: str                          # appkey, used for the token signature
    password: str
    loginname: Optional[str] = None        # GetLineFilterNow 的 loginname；留空则回退用 username
    token_url: str
    line_list_url: str
    line_entity_url: str
    token_path: Optional[str] = None       # dot-path to token in the response; None = heuristic
    line_name_path: Optional[str] = None   # dot-path to the line-name list; None = heuristic
    line_name_field: Optional[str] = None  # key in each list item holding the line name (e.g. "Roadline")
    company_field: str = "Company"         # key in each list item holding the company name
    companys: Optional[str] = None         # 逗号分隔；只处理这些公司的线路（留空=不限公司）
    lines: Optional[str] = None            # 逗号分隔；只处理这些指定线路号（留空=不限，配合 companys/limit）
    line_limit: int = 0                    # cap lines processed per run; 0 = all
    token_ttl_seconds: int = 3600

    def companys_set(self):
        return _split_csv(self.companys)

    def lines_set(self):
        return _split_csv(self.lines)

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        return _validate_cron(v)


class SqliteConfig(BaseModel):
    path: str = "./road_network.db"


class MysqlConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "amap"
    password: str = ""
    database: str = "road_network"
    charset: str = "utf8mb4"


class DatabaseConfig(BaseModel):
    type: Literal["sqlite", "mysql"] = "sqlite"
    sqlite: SqliteConfig = Field(default_factory=SqliteConfig)
    mysql: Optional[MysqlConfig] = None

    @model_validator(mode="after")
    def _require_mysql_block(self) -> "DatabaseConfig":
        if self.type == "mysql" and self.mysql is None:
            raise ValueError("database.type=mysql requires a 'mysql' block")
        return self


class RedisUses(BaseModel):
    latest_traffic_snapshot: bool = True
    incremental_detection: bool = True
    token_cache: bool = True


class RedisConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    uses: RedisUses = Field(default_factory=RedisUses)
    traffic_ttl_seconds: int = 600  # traffic:latest/sig 的过期时间（秒，默认10分钟；缺失自动回落DB）


class HttpConfig(BaseModel):
    timeout_seconds: int = 30
    max_retries: int = 3
    backoff_seconds: float = 2.0


class SdkConfig(BaseModel):
    match_tolerance_m: float = 30.0
    reverse_angle_deg: float = 90.0
    dedup_jitter_m: float = 5.0
    refine_passes: int = 2          # 二次匹配遍数（>=2：首遍结果加密后再匹，补 GPS 漂移缺口）
    densify_step_m: float = 15.0    # 二次匹配前的重采样步长（米）
    against_track_deg: float = 120.0  # 路段方向与原轨迹相差超此值即删（对面车道/反向）
    loop_return_m: float = 10.0       # 折返闭环判定：某段起点距更早某段终点小于此值即视为绕回
    jut_deg: float = 60.0             # 某段相对前后两段都偏转超此值（且前后同向）即判为垂直 jut 删除
    jut_neighbor_deg: float = 45.0    # 前后两段方向差小于此值才算"路线直行"（保护真转弯）
    jut_offtrack_m: float = 15.0      # jut 还需偏离原轨迹超此值才删（保护在轨迹上的真实绕行段）
    against_window_frac: float = 0.2  # 反向判定只比对轨迹的「局部一段」：总长的此比例
    against_window_m: float = 80.0    # ...且至少此米数（去/回程重叠时避免与对向腿误比）
    connect_gap_m: float = 8.0        # 相邻路段端点距超此值即视为有缺口，触发图上补链
    max_fill_links: int = 8           # 单个缺口最多补入的连接路段数（防绕远路）
    section_sample_step_m: float = 4.0  # section-build 站点对齐采样步长（米）


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: Optional[str] = None


class AppConfig(BaseModel):
    amap: AmapConfig
    transit: TransitConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    sdk: SdkConfig = Field(default_factory=SdkConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
