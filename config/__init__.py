"""MaiTrace v6 配置模型，按功能模块分段。

SDK 负责合并默认配置；这里只定义模板，不读取或改写实际 config.toml。
"""

from maibot_sdk import Field, PluginConfigBase

from .sections_proactive import RoutineSection
from .sections_qzone import ImageSection, LLMSection, MonitorSection, PersonaSection, PluginSection, ReadSection, SendSection
from ..services.life.config import EnvironmentSection, LifeSection, MessagingSection
from ..services.schedule.config import GoalCleanupSection, InjectSection, ScheduleSection


class MaiTracePluginConfig(PluginConfigBase):
    """MaiTrace 顶层配置。"""

    plugin: PluginSection = Field(default_factory=PluginSection)
    persona: PersonaSection = Field(default_factory=PersonaSection)
    llm: LLMSection = Field(default_factory=LLMSection)
    schedule: ScheduleSection = Field(default_factory=ScheduleSection)
    inject: InjectSection = Field(default_factory=InjectSection)
    goal_cleanup: GoalCleanupSection = Field(default_factory=GoalCleanupSection)
    life: LifeSection = Field(default_factory=LifeSection)
    environment: EnvironmentSection = Field(default_factory=EnvironmentSection)
    messaging: MessagingSection = Field(default_factory=MessagingSection)
    send: SendSection = Field(default_factory=SendSection)
    image: ImageSection = Field(default_factory=ImageSection)
    read: ReadSection = Field(default_factory=ReadSection)
    monitor: MonitorSection = Field(default_factory=MonitorSection)
    routine: RoutineSection = Field(default_factory=RoutineSection)
