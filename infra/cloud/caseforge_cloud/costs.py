"""Plain-language cost lines, printed before anything is created or started."""

from __future__ import annotations

from .clouds.base import Cloud

HOURS_PER_MONTH = 730


def estimate(cloud: Cloud, size: str, disk_gb: int) -> dict:
    hourly = cloud.hourly_usd(size) + cloud.running_extras_hourly_usd()
    disk = cloud.disk_month_usd(disk_gb)
    return {
        "running_hour": hourly + disk / HOURS_PER_MONTH,
        "running_day_8h": hourly * 8 + disk / 30,
        "running_month": hourly * HOURS_PER_MONTH + disk,
        "stopped_month": disk,
    }


def describe(cloud: Cloud, size: str, disk_gb: int) -> str:
    e = estimate(cloud, size, disk_gb)
    return (
        f"Estimated {cloud.label} cost ({size}, {disk_gb} GB disk, on-demand list prices):\n"
        f"  running:  about ${e['running_hour']:.2f} an hour, ${e['running_day_8h']:.2f} for an 8-hour day, "
        f"${e['running_month']:.0f} a month if never stopped\n"
        f"  stopped:  about ${e['stopped_month']:.0f} a month for the disk (data kept)\n"
        f"  down:     nothing (everything deleted)"
    )
