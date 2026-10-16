"""Cloud names/settings derived from config.yaml, shared by infra/*.sh and the controller.

    python -m src.cloud.settings      # prints KEY='value' lines; infra scripts `eval` them
"""
from __future__ import annotations

import shlex

from src.utils import load_config


def cloud_settings(cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    c, vm = cfg["cloud"], cfg["vm"]
    p = c["name_prefix"]
    return {
        "REGION": c["region"],
        "PREFIX": p,
        "TAG_KEY": c["tag_key"],
        "TAG_VALUE": c["tag_value"],
        "INSTANCE_TYPE": vm["size"],
        "AMI_PARAM": c["ami_ssm_parameter"],
        "ROOT_GB": c["root_volume_gb"],
        "VMS_PER_GROUP": vm["max_per_region"],
        "WORKER_GROUPS": " ".join(r["zone"] for r in cfg["regions"]),
        "TABLE": f"{p}-results",
        "ROLE": f"{p}-worker-role",
        "PROFILE": f"{p}-worker-profile",
        "SG_NAME": f"{p}-worker-sg",
        "QUEUE_PREFIX": f"{p}-jobs-",
        "VISIBILITY_TIMEOUT": c["queue_visibility_timeout_s"],
        # sim seconds of work per real second (1 sim hour = real_seconds_per_sim_hour)
        "SPEEDUP": 3600 / cfg["time_compression"]["real_seconds_per_sim_hour"],
    }


if __name__ == "__main__":
    for k, v in cloud_settings().items():
        print(f"{k}={shlex.quote(str(v))}")
