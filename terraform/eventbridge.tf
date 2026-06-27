# eventbridge.tf — daily schedule that invokes the pipeline Lambda.
#
# WHAT: an EventBridge rule on `var.schedule_expression` targeting the Lambda, plus
#       the lambda permission letting EventBridge invoke it.
# WHY:  v0 runs once daily (build-plan §v0-in-one-sentence). The cron lives in config.
# SO-WHAT: the only orchestration v0 needs (Step Functions is a later migration).

resource "aws_cloudwatch_event_rule" "daily" {
  name                = "jobfetcher-${var.env}-daily"
  description         = "Daily trigger for the jobfetcher pipeline."
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "daily_lambda" {
  rule      = aws_cloudwatch_event_rule.daily.name
  target_id = "jobfetcher-pipeline"
  arn       = aws_lambda_function.pipeline.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pipeline.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}
