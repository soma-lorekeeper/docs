#!/bin/zsh
set -euo pipefail

export AWS_PROFILE=lorekeeper AWS_REGION=ap-northeast-2
export PATH="/opt/homebrew/opt/libpq/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

HERE="${0:A:h}"
ROOT="${BACKUP_ROOT:-$HOME/loresentry-backup}"
DEST="$ROOT/prod-$(date +%Y-%m-%d-%H%M)"
PREV="${PREV:-}"
SKIP_UNWRAP="${SKIP_UNWRAP:-0}"
KEY_ALIAS=alias/lore-sentry-manuscript

umask 077
mkdir -p "$DEST/db" "$DEST/s3" "$DEST/keys" "$DEST/tools"
exec > >(tee -a "$DEST/backup.log") 2>&1
say() { print -r -- "[$(date +%H:%M:%S)] $*"; }

say "destination $DEST"
aws sts get-caller-identity --query Arn --output text
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
if ! telepresence status 2>/dev/null | grep -q "Status *: Connected"; then
  telepresence quit >/dev/null 2>&1 || true
  telepresence connect --context lore-sentry --namespace prod
fi

RDS=$(aws rds describe-db-instances --db-instance-identifier lore-sentry-postgres --query 'DBInstances[0].Endpoint.Address' --output text)
SECRET=$(aws rds describe-db-instances --db-instance-identifier lore-sentry-postgres --query 'DBInstances[0].MasterUserSecret.SecretArn' --output text)
CREDS=$(aws secretsmanager get-secret-value --secret-id "$SECRET" --query SecretString --output text)
export PGUSER=$(jq -r .username <<<"$CREDS") PGPASSWORD=$(jq -r .password <<<"$CREDS")
export PGOPTIONS="-c default_transaction_read_only=on" PGCONNECT_TIMEOUT=20

for db in authentication content ai_chat; do
  pg_dump "host=$RDS dbname=$db sslmode=require" -Fc --no-owner --no-privileges -f "$DEST/db/$db.dump"
  say "dumped $db ($(pg_restore -l "$DEST/db/$db.dump" | grep -c 'TABLE DATA') tables)"
done
unset PGPASSWORD

for b in media refresh; do
  aws s3 sync "s3://loresentry-$b-prod-$ACCOUNT" "$DEST/s3/$b" --only-show-errors
  say "synced $b ($(find "$DEST/s3/$b" -type f | wc -l | tr -d ' ') files)"
done

cp "$HERE"/*.py "$HERE"/backup.sh "$DEST/tools/"
if [[ -n "$PREV" && -f "$PREV/keys/project-keys.json" ]]; then
  cp "$PREV/keys/project-keys.json" "$DEST/keys/project-keys.json"
  say "reused $(jq length "$DEST/keys/project-keys.json") keys from $PREV"
fi

if [[ "$SKIP_UNWRAP" != 1 ]]; then
  KEY=$(aws kms describe-key --key-id $KEY_ALIAS --query KeyMetadata.KeyId --output text)
  ROLE_NAME=$(aws sts get-caller-identity --query Arn --output text | cut -d/ -f2)
  ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)
  ORIGINAL="$DEST/keys/key-policy.original.json"
  aws kms get-key-policy --key-id "$KEY" --policy-name default --query Policy --output text > "$ORIGINAL"
  if jq -e '.Statement[] | select(.Sid == "TemporaryBackupUnwrap")' "$ORIGINAL" >/dev/null; then
    say "key policy already holds TemporaryBackupUnwrap; refusing to treat it as the original"
    exit 1
  fi
  restore() {
    aws kms put-key-policy --key-id "$KEY" --policy-name default --policy "file://$ORIGINAL" && say "key policy restored"
  }
  trap restore EXIT
  TEMP=$(mktemp)
  jq --arg role "$ROLE_ARN" '.Statement += [{"Sid":"TemporaryBackupUnwrap","Effect":"Allow","Principal":{"AWS":$role},"Action":"kms:Decrypt","Resource":"*","Condition":{"StringLike":{"kms:EncryptionContext:project_id":"*"}}}]' "$ORIGINAL" > "$TEMP"
  aws kms put-key-policy --key-id "$KEY" --policy-name default --policy "file://$TEMP"
  rm -f "$TEMP"
  say "temporary decrypt statement added for $ROLE_ARN"
  sleep 30
  UNWRAPPED=0
  for round in 1 2 3; do
    if python3 "$DEST/tools/unwrap_keys.py" "$DEST" "$KEY"; then UNWRAPPED=1; break; fi
    sleep 20
  done
  restore
  trap - EXIT

  PROBE=$(mktemp)
  pg_restore -a -t project_keys -f - "$DEST/db/content.dump" | awk -F'\t' '/^[0-9a-f]+-/{print $1" "$2; exit}' > "$PROBE.txt"
  PROBE_ID=$(cut -d' ' -f1 "$PROBE.txt")
  cut -d' ' -f2 "$PROBE.txt" | sed 's/\\\\x//' | xxd -r -p > "$PROBE"
  DENIED=0
  for i in {1..18}; do
    sleep 10
    PROBE_OUT=$(aws kms decrypt --key-id "$KEY" --ciphertext-blob "fileb://$PROBE" --encryption-context "project_id=$PROBE_ID" --query KeyId --output text 2>&1 || true)
    if [[ "$PROBE_OUT" == *AccessDenied* ]]; then
      DENIED=$((DENIED + 1))
      [[ $DENIED -ge 3 ]] && break
    else
      DENIED=0
    fi
  done
  rm -f "$PROBE" "$PROBE.txt"
  if [[ $DENIED -ge 3 ]]; then say "decrypt is denied again"; else say "WARNING: decrypt still allowed, check the key policy"; fi
  [[ $UNWRAPPED == 1 ]] || { say "FAILED: some project keys were not unwrapped"; exit 1; }
fi

( cd "$DEST/tools" && python3 verify.py ) && say "verification passed" || { say "FAILED: verification"; exit 1; }
( cd "$DEST" && shasum -a 256 db/*.dump keys/project-keys.json > SHA256SUMS )
chmod -R go-rwx "$DEST"
say "done: $(du -sh "$DEST" | cut -f1)"
