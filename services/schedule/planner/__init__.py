"""Planner Module for Autonomous Planning Plugin.

This module provides core planning functionality including:
    - Goal management (create, update, track, delete goals)
    - Schedule generation (daily/weekly/monthly schedules)
    - Auto-scheduling with LLM integration

Main Components:
    Goal: Represents a single goal/task with status, priority, and execution tracking
    GoalManager: Manages all goals, handles persistence and cleanup
    Schedule: Represents a schedule (collection of scheduled items)
    ScheduleGenerator: Generates personalized schedules using LLM
    ScheduleAutoScheduler: Automated scheduler that runs at configured times

Usage:
    >>> from planner import GoalManager, ScheduleGenerator
    >>> goal_manager = GoalManager(data_dir="data/schedule")
    >>> generator = ScheduleGenerator(goal_manager)
"""

from .goal_manager import (
    Goal,
    GoalManager,
    GoalPriority,
    GoalStatus,
)
from .schedule_generator import (
    Schedule,
    ScheduleGenerator,
    ScheduleItem,
    ScheduleType,
)

__all__ = [
    "Goal",
    "GoalManager",
    "GoalStatus",
    "GoalPriority",
    "ScheduleGenerator",
    "Schedule",
    "ScheduleItem",
    "ScheduleType",
]
