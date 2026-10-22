from __future__ import annotations

import logging
import signal
import threading
from pathlib import Path
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .backfill import backfill
from .bootstrap import build_runner, load_agent_cases
from .config import AppConfig
from .db import connect, migrate
from .runner import EvaluationRunner

log = logging.getLogger(__name__)
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Schedule:
    def __init__(self, config: AppConfig, kind: str = "healthcheck"):
        if kind == "healthcheck":
            timezone = config.healthcheck_timezone
            schedules = config.healthcheck_schedules
            agents = config.healthcheck_agents
            weekdays = config.healthcheck_weekdays
            grace_minutes = config.misfire_grace_minutes
        elif kind == "evaluation":
            timezone = config.evaluation_timezone
            schedules = config.evaluation_schedules
            agents = config.evaluation_agents
            weekdays = config.evaluation_weekdays
            grace_minutes = config.evaluation_misfire_grace_minutes
        else:
            raise ValueError(f"Unknown schedule kind: {kind}")
        self.kind = kind
        self.timezone = ZoneInfo(timezone)
        self.slots = sorted({time.fromisoformat(s.strip()) for s in schedules.split(",") if s.strip()})
        if not self.slots or any(t.second or t.microsecond or t.tzinfo for t in self.slots):
            raise ValueError(f"{kind.upper()}_SCHEDULES must contain HH:MM slots")
        self.agents = list(dict.fromkeys(s.strip() for s in agents.split(",") if s.strip()))
        if not self.agents or set(self.agents) - config.agents.keys():
            raise ValueError(f"{kind.upper()}_AGENTS must name configured agents")
        spec = weekdays.strip().lower()
        if "-" in spec:
            start, end = spec.split("-")
            a, b = DAYS.index(start), DAYS.index(end)
            self.weekdays = list(range(a, b + 1)) if a <= b else list(range(a, 7)) + list(range(b + 1))
        else:
            self.weekdays = sorted({DAYS.index(d.strip()) for d in spec.split(",")})
        self.grace = timedelta(minutes=grace_minutes)
        if not 1 <= grace_minutes <= 1440:
            raise ValueError("Misfire grace must be between 1 and 1440 minutes")

    def due(self, now: datetime) -> list[datetime]:
        if now.tzinfo is None:
            raise ValueError("Scheduler clock must be timezone-aware")
        local = now.astimezone(self.timezone)
        slots = []
        # Includes the previous day when the grace interval crosses midnight.
        for day in (local.date() - timedelta(days=1), local.date()):
            if day.weekday() not in self.weekdays:
                continue
            for slot in self.slots:
                stamp = datetime.combine(day, slot, self.timezone)
                if timedelta(0) <= local - stamp <= self.grace:
                    slots.append(stamp)
        # Coalesce multiple missed slots to the latest one.
        return slots[-1:]


class HealthcheckScheduler:
    def __init__(self, config: AppConfig, runner=None):
        self.config, self.schedule = config, Schedule(config)
        self.runner = runner or EvaluationRunner(config)

    def configure(self):
        migrate(self.config.database_url, self.config.project_root)
        with connect(self.config.database_url) as conn:
            conn.execute(
                "INSERT INTO schedule_settings VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (environment) DO UPDATE SET timezone=EXCLUDED.timezone, slots=EXCLUDED.slots, "
                "weekdays=EXCLUDED.weekdays, agents=EXCLUDED.agents, grace_minutes=EXCLUDED.grace_minutes",
                (self.config.environment, self.config.healthcheck_timezone, self.schedule.slots,
                 [d + 1 for d in self.schedule.weekdays], self.schedule.agents, self.config.misfire_grace_minutes),
            )

    def tick(self, now: datetime) -> int:
        count = 0
        for scheduled in self.schedule.due(now):
            batch_id = f"{self.config.environment}_{scheduled.strftime('%Y%m%d_%H%M%S%z')}"
            # Unique environment+slot claim survives restarts and competing processes.
            # A session advisory lock prevents separate slots overlapping in one environment.
            with connect(self.config.database_url) as lock_conn:
                locked = lock_conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))",
                                           (f"healthcheck:{self.config.environment}",)).fetchone()[0]
                lock_conn.commit()
                if not locked:
                    continue
                try:
                    with connect(self.config.database_url) as conn:
                        claimed = conn.execute(
                            "INSERT INTO scheduled_batches (batch_id,environment,scheduled_for,expected_agents) "
                            "VALUES (%s,%s,%s,%s) ON CONFLICT (environment,scheduled_for) DO NOTHING RETURNING batch_id",
                            (batch_id, self.config.environment, scheduled, self.schedule.agents),
                        ).fetchone()
                    if not claimed:
                        continue
                    completed = 0
                    for agent in self.schedule.agents:
                        try:
                            self.runner.healthcheck(agent, batch_id=batch_id, scheduled_for=scheduled.isoformat())
                            completed += 1
                        except Exception:
                            log.error("Batch %s could not persist result for %s", batch_id, agent)
                    with connect(self.config.database_url) as conn:
                        conn.execute("UPDATE scheduled_batches SET finished_at=now(),status=%s WHERE batch_id=%s",
                                     ("completed" if completed == len(self.schedule.agents) else "partial", batch_id))
                    count += 1
                    log.info("Batch %s finished: %s/%s results", batch_id, completed, len(self.schedule.agents))
                finally:
                    lock_conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (f"healthcheck:{self.config.environment}",))
        return count

    def serve(self, once=False):
        _serve(self, "Healthcheck", "scheduler-heartbeat", once)


class EvaluationScheduler:
    def __init__(self, config: AppConfig, runner=None):
        self.config = config
        self.schedule = Schedule(config, "evaluation")
        self.runner = runner or build_runner(config)
        self.cases = load_agent_cases(config, self.schedule.agents)

    def configure(self):
        migrate(self.config.database_url, self.config.project_root)
        with connect(self.config.database_url) as conn:
            conn.execute(
                "INSERT INTO evaluation_schedule_settings VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (environment) DO UPDATE SET timezone=EXCLUDED.timezone, slots=EXCLUDED.slots, "
                "weekdays=EXCLUDED.weekdays, agents=EXCLUDED.agents, cases=EXCLUDED.cases, "
                "grace_minutes=EXCLUDED.grace_minutes",
                (self.config.environment, self.config.evaluation_timezone, self.schedule.slots,
                 [d + 1 for d in self.schedule.weekdays], self.schedule.agents,
                 [case.id for case in self.cases],
                 self.config.evaluation_misfire_grace_minutes),
            )

    def tick(self, now: datetime) -> int:
        count = 0
        for scheduled in self.schedule.due(now):
            batch_id = f"eval_{self.config.environment}_{scheduled.strftime('%Y%m%d_%H%M%S%z')}"
            with connect(self.config.database_url) as lock_conn:
                locked = lock_conn.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s))",
                    (f"evaluation:{self.config.environment}",),
                ).fetchone()[0]
                lock_conn.commit()
                if not locked:
                    continue
                try:
                    with connect(self.config.database_url) as conn:
                        claimed = conn.execute(
                            "INSERT INTO scheduled_evaluation_batches "
                            "(batch_id,environment,scheduled_for,expected_cases) VALUES (%s,%s,%s,%s) "
                            "ON CONFLICT (environment,scheduled_for) DO NOTHING RETURNING batch_id",
                            (batch_id, self.config.environment, scheduled, [case.id for case in self.cases]),
                        ).fetchone()
                    if not claimed:
                        continue
                    completed = 0
                    for case in self.cases:
                        try:
                            self.runner.run_case(
                                case,
                                batch_id=batch_id,
                                scheduled_for=scheduled.isoformat(),
                            )
                            completed += 1
                        except Exception:
                            log.error("Evaluation batch %s could not persist result for %s", batch_id, case.id)
                    with connect(self.config.database_url) as conn:
                        conn.execute(
                            "UPDATE scheduled_evaluation_batches SET finished_at=now(),status=%s WHERE batch_id=%s",
                            ("completed" if completed == len(self.cases) else "partial", batch_id),
                        )
                    count += 1
                    log.info("Evaluation batch %s finished: %s/%s cases", batch_id, completed, len(self.cases))
                finally:
                    lock_conn.execute(
                        "SELECT pg_advisory_unlock(hashtext(%s))",
                        (f"evaluation:{self.config.environment}",),
                    )
        return count

    def serve(self, once=False):
        _serve(self, "Evaluation", "evaluation-scheduler-heartbeat", once)


def _serve(scheduler, label: str, heartbeat_name: str, once: bool) -> None:
    scheduler.configure()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    slots = ",".join(slot.strftime("%H:%M") for slot in scheduler.schedule.slots)
    log.info("%s scheduler ready: %s (%s)", label, slots, scheduler.schedule.timezone.key)
    replay_at = None
    heartbeat = Path(f"/tmp/{heartbeat_name}" if Path("/tmp").exists() else heartbeat_name)
    while not stop.is_set():
        now = datetime.now(scheduler.schedule.timezone)
        try:
            heartbeat.touch()
            scheduler.tick(now)
            if replay_at is None or now >= replay_at:
                saved, failed = backfill(scheduler.config, scheduler.config.runs_dir, only_missing=True)
                if saved or failed:
                    log.info("JSON recovery: %s saved, %s failed", saved, failed)
                replay_at = now + timedelta(minutes=5)
            heartbeat.touch()
        except Exception:
            log.exception("%s scheduler/database operation failed; retrying in 15 seconds", label)
            if once:
                raise
        if once:
            return
        stop.wait(15)
