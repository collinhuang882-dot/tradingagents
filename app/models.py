from datetime import date, datetime
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
STAGES = ['数据核验', '板块异动', 'K 线与成交量', '政策与外围', '多头证据', '空头反证', '风险审查', '报告整合']
TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}


def now():
    return datetime.now(TZ)


class ReviewInput(BaseModel):
    trade_date: date
    as_of: datetime
    stocks: list[str] = Field(default_factory=list, max_length=10)
    sectors: list[str] = Field(default_factory=list, max_length=5)
    mode: Literal['live', 'demo'] = 'live'
    execution_mode: Literal['workflow', 'autonomous'] = 'workflow'
    focus: list[str] = Field(default_factory=list, max_length=10)
    conversation_id: str | None = None
    parent_run_id: str | None = None
    research_question: str = Field(default='', max_length=8000)
    research_depth: Literal['auto', 'quick', 'deep'] = 'auto'
    data_policy: Literal['auto', 'local_only'] = 'auto'
    horizon: str = Field(default='截至复盘时点的盘后研究（投资周期未指定）', max_length=200)
    research_context: dict = Field(default_factory=dict)
    parent_artifact_id: str | None = None
    document_urls: list[str] = Field(default_factory=list, max_length=3)
    user_material: str = Field(default='', max_length=8000)
    lookback: int = Field(default=7, ge=1, le=30)

    @model_validator(mode='after')
    def validate_scope(self):
        self.stocks = list(dict.fromkeys(self.stocks))
        self.sectors = list(dict.fromkeys(self.sectors))
        if not self.stocks and not self.sectors:
            raise ValueError('至少选择一只股票或一个行业')
        if self.as_of.tzinfo is None:
            raise ValueError('截止时间必须包含时区')
        self.as_of = self.as_of.astimezone(TZ)
        if self.as_of.date() != self.trade_date:
            raise ValueError('截止时间必须属于所选交易日')
        if self.as_of > now():
            raise ValueError('截止时间不能晚于当前时间')
        if self.as_of.hour < 15:
            raise ValueError('正式盘后复盘的截止时间不得早于 15:00')
        return self


class ReportFilters(BaseModel):
    investment_side: Literal['all','bull','bear'] = 'all'
    investment_status: Literal['all','supported','conditional','unresolved','refuted','withdrawn'] = 'all'
    investment_subject: str = 'all'
    investment_type: Literal['all','fact','derived','inference','hypothesis'] = 'all'
    stocks: list[str] = Field(default_factory=list)
    sectors: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    q: str = ''
    sort: str = 'code'
    descending: bool = False
    issuers: list[str] = Field(default_factory=list)
    kinds: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    qualities: list[str] = Field(default_factory=list)
    event_start: str = ''
    event_end: str = ''
    body_available: str = ''
    fulltext: bool = False
    all_evidence: bool = False


class ExportInput(BaseModel):
    format: Literal['docx', 'md'] = 'docx'
    scope: Literal['full', 'filtered'] = 'full'
    charts: bool = True
    filters: ReportFilters = Field(default_factory=ReportFilters)


class SettingsInput(BaseModel):
    ifind_enabled: bool | None = None
    ifind_access_token: str | None = None
    ifind_refresh_token: str | None = None
    tushare_token: str | None = None
    llm_key: str | None = None
    llm_url: str = 'https://api.deepseek.com/v1'
    llm_model: str = 'deepseek-chat'


class Claim(BaseModel):
    subject: str
    text: str = Field(max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    condition: str = Field(default='', max_length=500)
    limitation: str = Field(default='', max_length=500)


class AgentOutput(BaseModel):
    claims: list[Claim] = Field(min_length=1, max_length=30)

