#!/usr/bin/env bash
# Delete every CarbonShift AWS resource created by infra/create.sh — and nothing else.
# Instances are selected by tag project=carbonshift; the other resources by their exact carbonshift-* names.
#   infra/teardown.sh          lists what will be deleted, asks for confirmation
#   infra/teardown.sh --yes    no prompt
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_PAGER=""
eval "$(.venv/bin/python -m src.cloud.settings)"
export AWS_REGION="$REGION"
log() { printf '\n== %s\n' "$*"; }

IDS=$(aws ec2 describe-instances --query 'Reservations[].Instances[].InstanceId' --output text \
  --filters "Name=tag:$TAG_KEY,Values=$TAG_VALUE" Name=instance-state-name,Values=pending,running,stopping,stopped)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$SG_NAME" "Name=tag:$TAG_KEY,Values=$TAG_VALUE" \
  --query 'SecurityGroups[0].GroupId' --output text)
QUEUES=$(for g in $WORKER_GROUPS; do aws sqs get-queue-url --queue-name "$QUEUE_PREFIX$g" --query QueueUrl --output text 2>/dev/null || true; done)
HAS_TABLE=$(aws dynamodb describe-table --table-name "$TABLE" --query Table.TableName --output text 2>/dev/null || true)
HAS_ROLE=$(aws iam get-role --role-name "$ROLE" --query Role.RoleName --output text 2>/dev/null || true)
# instance profiles holding the role (created by create.sh as $PROFILE, or by the IAM console under the role's name)
PROFILES=$(aws iam list-instance-profiles-for-role --role-name "$ROLE" --query 'InstanceProfiles[].InstanceProfileName' --output text 2>/dev/null || true)

cat <<EOF
Region $REGION — will DELETE:
  EC2 instances (+ their disks): ${IDS:-none}
  Security group:                ${SG/None/none}
  SQS queues:                    ${QUEUES:-none}
  DynamoDB table:                ${HAS_TABLE:-none}   (job results — export them first: python -m src.cloud.controller --export)
  IAM role / instance profile:   ${HAS_ROLE:-none} / ${PROFILES:-none}
EOF
if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Delete all of the above? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "aborted"; exit 1; }
fi

if [[ -n "$IDS" ]]; then
  log "Terminating instances"; aws ec2 terminate-instances --instance-ids $IDS >/dev/null
  aws ec2 wait instance-terminated --instance-ids $IDS
fi
if [[ "$SG" != "None" ]]; then log "Security group"; aws ec2 delete-security-group --group-id "$SG"; fi
for q in $QUEUES; do log "Queue $q"; aws sqs delete-queue --queue-url "$q"; done
if [[ -n "$HAS_TABLE" ]]; then log "DynamoDB table"; aws dynamodb delete-table --table-name "$TABLE" >/dev/null; fi
for p in $PROFILES; do
  [[ "$p" == "$PREFIX"* ]] || { echo "skipping instance profile $p (not $PREFIX-*)"; continue; }
  log "Instance profile $p"
  aws iam remove-role-from-instance-profile --instance-profile-name "$p" --role-name "$ROLE"
  aws iam delete-instance-profile --instance-profile-name "$p"
done
if [[ -n "$HAS_ROLE" ]]; then
  log "IAM role"
  aws iam delete-role-policy --role-name "$ROLE" --policy-name "$PREFIX-worker" 2>/dev/null || true
  aws iam detach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore 2>/dev/null || true
  aws iam delete-role --role-name "$ROLE"
fi
log "Teardown complete"
