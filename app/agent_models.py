from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .models import Claim


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


Kind = Literal['fetch_market', 'fetch_sector', 'search_policy', 'fetch_document',
               'fetch_global', 'search_announcement', 'validate_data', 'compare_sources', 'analyze_market', 'analyze_sector', 'analyze_policy',
               'argue_bull', 'argue_bear', 'rebut_theses', 'adjudicate_theses', 'review_risk']


class Task(Strict):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    kind: Kind
    subjects: list[str] = Field(default_factory=list, max_length=15)
    source_id: str = ''
    query_terms: list[str] = Field(default_factory=list, max_length=8)
    depends_on: list[str] = Field(default_factory=list, max_length=40)
    input_refs: list[str] = Field(default_factory=list, max_length=40)
    gap_id: str = ''
    dispute_id: str = ''
    trigger_event_id: str = ''
    priority: int = Field(default=50, ge=0, le=100)
    url: str = Field(default='', max_length=2000)


class Add(Strict):
    type: Literal['add_task']
    task: Task
    reason: str = Field(min_length=1, max_length=500)


class Revise(Strict):
    type: Literal['revise_task']
    task_id: str
    task: Task
    reason: str = Field(min_length=1, max_length=500)


class Skip(Strict):
    type: Literal['skip_task']
    task_id: str
    reason: str = Field(min_length=1, max_length=500)


class Finish(Strict):
    type: Literal['finish']
    reason: str = Field(min_length=1, max_length=500)


class Plan(Strict):
    base_plan_version: int = Field(ge=0)
    actions: list[Add | Revise | Skip | Finish] = Field(min_length=1, max_length=12)


class Analysis(Strict):
    outcome: Literal['supported', 'abstained']
    claims: list[Claim] = Field(default_factory=list, max_length=3)
    gaps: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode='after')
    def consistent(self):
        if self.outcome == 'supported' and not self.claims:
            raise ValueError('supported 必须有结论')
        if self.outcome == 'abstained' and (self.claims or not self.gaps):
            raise ValueError('abstained 必须有缺口且无结论')
        return self


class ConversationCreate(Strict):
    mode: Literal['live', 'demo'] = 'live'
    run_id: str | None = None
    report_id: str | None = None


class MessageCreate(Strict):
    text: str = Field(min_length=1, max_length=8000)
    client_message_id: str = Field(min_length=1, max_length=100)
    base_requirement_version: int | None = Field(default=None, ge=0)
    base_context_version: int | None = Field(default=None, ge=0)
    thread_id: str | None = None
    reply_to: str | None = None
    question_id: str | None = None
    research_policy: Literal['inherit', 'auto', 'local_only'] = 'inherit'
    research_depth: Literal['auto', 'quick', 'deep'] = 'auto'


    @model_validator(mode='after')
    def message_version(self):
        if not self.text.strip(): raise ValueError('消息不能为空')
        if self.base_requirement_version is None and self.base_context_version is None:
            raise ValueError('必须提供上下文版本')
        if self.base_requirement_version is not None and self.base_context_version is not None and self.base_requirement_version != self.base_context_version:
            raise ValueError('上下文版本字段冲突')
        if self.base_requirement_version is None: self.base_requirement_version = self.base_context_version
        return self


class Intent(Strict):
    research_depth: Literal['auto', 'quick', 'deep'] = 'auto'
    horizon: str = Field(default='', max_length=200)
    type: Literal['create_review', 'explain_report', 'supplement_analysis', 'update_focus',
                  'get_status', 'cancel_run', 'clarify_answer', 'filter_results', 'export_result']
    stocks: list[str] = Field(default_factory=list, max_length=10)
    sectors: list[str] = Field(default_factory=list, max_length=5)
    trade_date: str | None = None
    as_of: str | None = None
    lookback: int = Field(default=7, ge=1, le=30)
    focus: list[str] = Field(default_factory=list, max_length=10)
    execution_mode: Literal['workflow', 'autonomous'] = 'autonomous'
    question: str = Field(default='', max_length=1000)
    clarification: str = Field(default='', max_length=1000)
    urls: list[str] = Field(default_factory=list, max_length=3)
    sort_field: Literal['name','close','pct_change','amount','volume_ratio'] = 'amount'
    descending: bool = True
    only_up: bool = False
    restore: bool = False


class Interpretation(Strict):
    intents: list[Intent] = Field(min_length=1, max_length=3)


class Answer(Strict):
    claims: list[Claim] = Field(default_factory=list, max_length=10)
    answer: str = Field(min_length=1, max_length=5000)
    citations: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=10)
    research_needed: bool = False
    research_reason: str = Field(default='', max_length=1000)
