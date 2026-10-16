#!/usr/bin/env bash
# Create the CarbonShift AWS resources (region and names come from config.yaml).
# Everything is named carbonshift-* and tagged project=carbonshift, so infra/teardown.sh can
# find and delete exactly these resources and nothing else in the account.
#
#   infra/create.sh --plan    print what would be created (read-only AWS calls only)
#   infra/create.sh           create (asks for confirmation), wait for workers, then STOP the VMs
#
# Safe to re-run: existing resources are detected and skipped.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_PAGER=""
eval "$(.venv/bin/python -m src.cloud.settings)"
export AWS_REGION="$REGION"
TAGS="Key=$TAG_KEY,Value=$TAG_VALUE"
log() { printf '\n== %s\n' "$*"; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
AMI=$(aws ssm get-parameter --name "$AMI_PARAM" --query Parameter.Value --output text)
ROOT_DEV=$(aws ec2 describe-images --image-ids "$AMI" --query 'Images[0].RootDeviceName' --output text)
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
N_VMS=$(( $(echo "$WORKER_GROUPS" | wc -w) * VMS_PER_GROUP ))

cat <<EOF
Account $ACCOUNT | region $REGION | default VPC $VPC | AMI $AMI (Canonical Ubuntu 24.04)
  IAM role + profile  $ROLE / $PROFILE
                      (SQS receive/delete on ${QUEUE_PREFIX}*, DynamoDB PutItem on $TABLE, SSM Session Manager)
  Security group      $SG_NAME  (NO inbound rules; outbound only)
  DynamoDB table      $TABLE  (on-demand billing, key job_id)
  SQS queues          $(for g in $WORKER_GROUPS; do printf '%s ' "$QUEUE_PREFIX$g"; done)
  EC2 instances       $N_VMS x $INSTANCE_TYPE ($VMS_PER_GROUP per group: $WORKER_GROUPS), ${ROOT_GB} GB gp3 disk, public IPv4
                      -> stopped automatically once every worker has reported in
EOF
[[ "${1:-}" == "--plan" ]] && exit 0
if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Create these resources? [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" ]] || { echo "aborted"; exit 1; }
fi

log "IAM role $ROLE"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --tags "$TAGS" --description "CarbonShift worker VMs" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name "$PREFIX-worker" --policy-document "{
  \"Version\": \"2012-10-17\",
  \"Statement\": [
    {\"Effect\": \"Allow\",
     \"Action\": [\"sqs:ReceiveMessage\", \"sqs:DeleteMessage\", \"sqs:ChangeMessageVisibility\", \"sqs:GetQueueAttributes\"],
     \"Resource\": \"arn:aws:sqs:$REGION:$ACCOUNT:${QUEUE_PREFIX}*\"},
    {\"Effect\": \"Allow\", \"Action\": [\"dynamodb:PutItem\"],
     \"Resource\": \"arn:aws:dynamodb:$REGION:$ACCOUNT:table/$TABLE\"}
  ]}"
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
if ! aws iam get-instance-profile --instance-profile-name "$PROFILE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$PROFILE" --tags "$TAGS" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE" --role-name "$ROLE"
fi

log "Security group $SG_NAME"
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$SG_NAME" Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [[ "$SG" == "None" ]]; then
  SG=$(aws ec2 create-security-group --group-name "$SG_NAME" --vpc-id "$VPC" \
    --description "CarbonShift workers - no inbound access" \
    --tag-specifications "ResourceType=security-group,Tags=[{$TAGS}]" --query GroupId --output text)
fi
echo "$SG"

log "DynamoDB table $TABLE"
if ! aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  aws dynamodb create-table --table-name "$TABLE" --billing-mode PAY_PER_REQUEST \
    --attribute-definitions AttributeName=job_id,AttributeType=S --key-schema AttributeName=job_id,KeyType=HASH \
    --tags "$TAGS" >/dev/null
  aws dynamodb wait table-exists --table-name "$TABLE"
fi

log "SQS queues"
for g in $WORKER_GROUPS; do
  aws sqs create-queue --queue-name "$QUEUE_PREFIX$g" --tags "$TAG_KEY=$TAG_VALUE" \
    --attributes "VisibilityTimeout=$VISIBILITY_TIMEOUT,MessageRetentionPeriod=86400,ReceiveMessageWaitTimeSeconds=20" \
    --query QueueUrl --output text
done

# cloud-init script: install boto3, write the worker, run it as a systemd service (starts on every boot)
user_data() {  # $1 = group, $2 = queue URL
  cat <<EOF
#!/bin/bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-boto3
mkdir -p /opt/carbonshift
cat > /opt/carbonshift/worker.py <<'PYEOF'
$(cat src/cloud/worker.py)
PYEOF
cat > /etc/systemd/system/carbonshift-worker.service <<'UNIT'
[Unit]
Description=CarbonShift worker
After=network-online.target
Wants=network-online.target
[Service]
Environment=CS_GROUP=$1 CS_QUEUE_URL=$2 CS_TABLE=$TABLE CS_SPEEDUP=$SPEEDUP AWS_DEFAULT_REGION=$REGION
ExecStart=/usr/bin/python3 -u /opt/carbonshift/worker.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now carbonshift-worker
EOF
}

log "EC2 instances"
for g in $WORKER_GROUPS; do
  QURL=$(aws sqs get-queue-url --queue-name "$QUEUE_PREFIX$g" --query QueueUrl --output text)
  for i in $(seq 1 "$VMS_PER_GROUP"); do
    NAME="$PREFIX-$g-$i"
    EXISTING=$(aws ec2 describe-instances --query 'Reservations[].Instances[].InstanceId' --output text \
      --filters Name=tag:Name,Values="$NAME" "Name=tag:$TAG_KEY,Values=$TAG_VALUE" \
                Name=instance-state-name,Values=pending,running,stopping,stopped)
    if [[ -n "$EXISTING" ]]; then echo "$NAME already exists ($EXISTING)"; continue; fi
    for attempt in 1 2 3 4 5 6; do   # a new instance profile takes a few seconds to become usable
      if ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$INSTANCE_TYPE" --count 1 \
          --iam-instance-profile Name="$PROFILE" --security-group-ids "$SG" \
          --metadata-options HttpTokens=required,HttpEndpoint=enabled \
          --credit-specification CpuCredits=unlimited --instance-initiated-shutdown-behavior stop \
          --block-device-mappings "DeviceName=$ROOT_DEV,Ebs={VolumeSize=$ROOT_GB,VolumeType=gp3,DeleteOnTermination=true}" \
          --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME},{$TAGS},{Key=group,Value=$g}]" \
                               "ResourceType=volume,Tags=[{Key=Name,Value=$NAME},{$TAGS}]" \
          --user-data "$(user_data "$g" "$QURL")" --query 'Instances[0].InstanceId' --output text 2>/tmp/cs_err); then
        echo "$NAME -> $ID"; break
      fi
      echo "  launch attempt $attempt failed: $(tail -1 /tmp/cs_err)"; sleep 10
      [[ $attempt == 6 ]] && { echo "giving up on $NAME"; exit 1; }
    done
  done
done

IDS=$(aws ec2 describe-instances --query 'Reservations[].Instances[].InstanceId' --output text \
  --filters "Name=tag:$TAG_KEY,Values=$TAG_VALUE" Name=instance-state-name,Values=pending,running)
if [[ -n "$IDS" ]]; then
  log "Waiting for instances to run and workers to report in (2-5 min)"
  aws ec2 wait instance-running --instance-ids $IDS
  for _ in $(seq 1 40); do
    UP=$(aws dynamodb scan --table-name "$TABLE" --select COUNT --query Count --output text \
      --filter-expression "kind = :k" --expression-attribute-values '{":k":{"S":"heartbeat"}}')
    echo "  workers ready: $UP / $N_VMS"
    [[ "$UP" -ge "$N_VMS" ]] && break
    sleep 15
  done
  if [[ "$UP" -lt "$N_VMS" ]]; then
    echo "WARNING: not all workers reported in. VMs left RUNNING for debugging; run infra/vms.sh stop when done."
    exit 1
  fi
  log "All workers ready - stopping VMs until the demo (no compute charge while stopped)"
  aws ec2 stop-instances --instance-ids $IDS >/dev/null
  aws ec2 wait instance-stopped --instance-ids $IDS
fi
infra/vms.sh status
