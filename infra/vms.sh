#!/usr/bin/env bash
# Start / stop / list the CarbonShift worker VMs (only instances tagged project=carbonshift).
#   infra/vms.sh status | start | stop
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_PAGER=""
eval "$(.venv/bin/python -m src.cloud.settings)"
export AWS_REGION="$REGION"
FILTER=("Name=tag:$TAG_KEY,Values=$TAG_VALUE" "Name=instance-state-name,Values=pending,running,stopping,stopped")

ids() { aws ec2 describe-instances --filters "${FILTER[@]}" --query 'Reservations[].Instances[].InstanceId' --output text; }

case "${1:-status}" in
  status)
    aws ec2 describe-instances --filters "${FILTER[@]}" --output table \
      --query 'Reservations[].Instances[].{Name:Tags[?Key==`Name`]|[0].Value,Id:InstanceId,State:State.Name,Type:InstanceType}' ;;
  start)
    I=$(ids); [[ -z "$I" ]] && { echo "no CarbonShift VMs"; exit 0; }
    aws ec2 start-instances --instance-ids $I >/dev/null && aws ec2 wait instance-running --instance-ids $I && echo "running: $I" ;;
  stop)
    I=$(ids); [[ -z "$I" ]] && { echo "no CarbonShift VMs"; exit 0; }
    aws ec2 stop-instances --instance-ids $I >/dev/null && aws ec2 wait instance-stopped --instance-ids $I && echo "stopped: $I" ;;
  *) echo "usage: $0 status|start|stop"; exit 1 ;;
esac
