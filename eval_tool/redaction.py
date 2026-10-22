from __future__ import annotations

import os
import re


class Redactor:
    def __init__(self, config):
        self.values = set()
        for key, value in os.environ.items():
            if any(part in key.upper() for part in ("KEY", "BEARER", "PASSWORD", "DATABASE_URL", "TOKEN")) and len(value) >= 4:
                self.values.add(value)
        for agent in config.agents.values():
            for key, value in agent.default_inputs.items():
                if any(part in key.lower() for part in ("name", "email", "emp", "token")) and value:
                    self.values.add(str(value))

    def text(self, value: str | None) -> str | None:
        if value is None:
            return None
        for secret in sorted(self.values, key=len, reverse=True):
            value = re.sub(re.escape(secret), "[REDACTED]", value, flags=re.IGNORECASE)
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[EMAIL]", value)
        value = re.sub(r"(?i)\b(?:Bearer\s+\S+|app-[A-Za-z0-9_-]+)", "[CREDENTIAL]", value)
        value = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "[ID]", value)
        value = re.sub(r"(?i)\b(emp_?id|submitRef|confirmToken|calendar_?id)\b\s*[:=]?\s*[\"']?[\w-]+", r"\1=[REDACTED]", value)
        value = re.sub(r"\b\d{4,}\b", "[NUMBER]", value)
        return value
