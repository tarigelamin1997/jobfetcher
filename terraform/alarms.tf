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
# the dead-man above owns that case. Honest gap: the handler catches stage failures and RETURNS
# `statusCode: 500` — a SUCCESSFUL invocation to Lambda, invisible here. Catching returned 500s
# needs a custom metric (a documented future step, built when a real miss justifies it).
resource "aws_cloudwatch_metric_alarm" "pipeline_errors" {
  alarm_name        = "jobfetcher-${var.env}-pipeline-errors"
  alarm_description = "The pipeline Lambda crashed or timed out (>= 1 error in the last hour). Returned statusCode-500 summaries are NOT counted — only unhandled crashes/timeouts."

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
