# alarms.tf — minimal operational alerting (Run 5).
#
# WHAT: one SNS topic (email to the digest recipient) + two CloudWatch alarms:
#       a DEAD-MAN on the daily EventBridge rule (the pipeline stopped being invoked)
#       and an ERRORS alarm on the Lambda (the pipeline crashed/timed out).
# WHY:  the failure mode a daily-digest tool can't self-report is SILENCE — no email could
#       mean "no matches today" or "the schedule/function is dead". These two alarms split
#       that ambiguity with zero new runtime components.
# SO-WHAT: a dead pipeline announces itself within ~a day instead of being discovered a week
#       later as an empty inbox.
#
# NOTE: the email subscription is NOT active until the recipient clicks the SNS confirmation
# link (docs/runbooks/deploy.md §4) — until then both alarms fire into the void.

resource "aws_sns_topic" "alarms" {
  name = "jobfetcher-${var.env}-alarms"
}

resource "aws_sns_topic_subscription" "alarms_email" {
  topic_arn = aws_sns_topic.alarms.arn
  protocol  = "email"
  endpoint  = var.recipient_email
}

# DEAD-MAN: the daily rule invoked nothing for a full day. AWS/Events `Invocations` is a
# SPARSE metric — it emits a datapoint only when the rule fires — so "missing data" IS the
# dead signal: `treat_missing_data = "breaching"` turns a silent day into an alarm.
#
# Evaluation-constraint check (validated against CloudWatch's PutMetricAlarm limits):
# `period` max is 86400s (1 day) and `period × evaluation_periods` may not exceed 86400s —
# so `86400 × 1` is the ONLY shape that watches a full day, and the minimal one.
#
# Detection-lag honesty: 1-day periods align to UTC-midnight windows and the current partial
# window isn't judged until it closes. The cron fires 06:00 UTC; if day D's run never happens,
# the day-D window can only be evaluated as empty after it closes — so the alert lands up to
# ~1 day after the missed run, i.e. worst case ~2 days after the LAST SUCCESSFUL invocation.
# That lag is acceptable for a daily digest; a tighter bound would need a second scheduled
# rule (more moving parts than the problem earns — P1).
resource "aws_cloudwatch_metric_alarm" "pipeline_dead_man" {
  alarm_name        = "jobfetcher-${var.env}-pipeline-dead-man"
  alarm_description = "The daily EventBridge rule invoked nothing for a full day — the pipeline is silently dead (schedule disabled, rule deleted, or permission broken)."

  namespace   = "AWS/Events"
  metric_name = "Invocations"
  dimensions = {
    RuleName = aws_cloudwatch_event_rule.daily.name
  }

  statistic           = "Sum"
  period              = 86400
  evaluation_periods  = 1
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  treat_missing_data  = "breaching" # no datapoint = the rule never fired = the dead signal

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn] # recovery notification: the schedule is alive again
}

# ERRORS: the Lambda crashed or timed out (an unhandled exception / hard timeout increments
# AWS/Lambda `Errors`). `notBreaching` on missing data because NO invocations is not a crash —
# the dead-man above owns that case. This alarm covers crashes/timeouts ONLY; a handler that
# CATCHES a stage failure and RETURNS `statusCode: 500` is a SUCCESSFUL invocation to Lambda,
# invisible here — that gap is now closed by the log-metric-filter alarm below (was the
# ADR-0029 "documented future step").
resource "aws_cloudwatch_metric_alarm" "pipeline_errors" {
  alarm_name        = "jobfetcher-${var.env}-pipeline-errors"
  alarm_description = "The pipeline Lambda crashed or timed out (>= 1 error in the last hour). Returned statusCode-500 summaries are caught by the pipeline-returned-500 alarm, not here."

  namespace   = "AWS/Lambda"
  metric_name = "Errors"
  dimensions = {
    FunctionName = aws_lambda_function.pipeline.function_name
  }

  statistic           = "Sum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching" # no invocations ≠ a crash; the dead-man covers silence

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn] # recovery notification
}

# RETURNED statusCode:500 — the gap the AWS/Lambda Errors alarm above can't see (a returned 500 is
# a *successful* invocation). We manage the pipeline's log group so a metric filter has a
# guaranteed target (Lambda auto-creates it otherwise; on an already-running stack import it once:
# `terraform import aws_cloudwatch_log_group.pipeline /aws/lambda/jobfetcher-<env>-pipeline`).
# retention_in_days also bounds today's unbounded log storage.
resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/lambda/jobfetcher-${var.env}-pipeline"
  retention_in_days = 30
}

# The handler emits `PIPELINE_ALARM` ONLY on an unattended daily-run 500 (smoke/reassess excluded,
# so the alarm never cries wolf — see handlers/pipeline.py). This filter turns that log line into a
# custom metric. Quoted text term (the logs are unstructured Lambda-default text, not JSON).
resource "aws_cloudwatch_log_metric_filter" "pipeline_returned_500" {
  name           = "jobfetcher-${var.env}-pipeline-returned-500"
  log_group_name = aws_cloudwatch_log_group.pipeline.name
  pattern        = "\"PIPELINE_ALARM\""

  metric_transformation {
    name          = "PipelineReturned500"
    namespace     = "JobFetcher/Pipeline"
    value         = "1"
    default_value = "0" # emit 0 on non-matching evaluation windows → the alarm has data to read
  }
}

# Page when the daily pipeline RETURNS a 500 (>= 1 marker in the window). Same SNS topic + email as
# the other two alarms. notBreaching on missing data: no marker = healthy (the dead-man owns "the
# run didn't happen at all").
resource "aws_cloudwatch_metric_alarm" "pipeline_returned_500" {
  alarm_name        = "jobfetcher-${var.env}-pipeline-returned-500"
  alarm_description = "The unattended daily pipeline caught a stage failure and RETURNED statusCode:500 (invisible to the AWS/Lambda Errors alarm). Smoke/reassess 500s are excluded at the source."

  namespace   = "JobFetcher/Pipeline"
  metric_name = "PipelineReturned500"

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "notBreaching" # no marker = no returned-500; the dead-man covers silence

  alarm_actions = [aws_sns_topic.alarms.arn]
  ok_actions    = [aws_sns_topic.alarms.arn] # recovery notification
}
