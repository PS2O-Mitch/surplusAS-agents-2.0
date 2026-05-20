# Webhook dead-letter alerting (Phase 7d).
#
# The gateway's `_webhook_retry_loop` (service/app.py) emits a structlog
# INFO line tagged `webhook_retry_sweep` after every sweep with the
# summary dict {scanned, retried, succeeded, failed, dead_lettered}.
# We turn that into a Cloud Logging metric and alert when any sweep
# observes dead_lettered > 0 — meaning at least one delivery hit
# attempt=5 with delivered_at IS NULL.
#
# Customers do not see this: dead-letter rows sit in agents.webhook_deliveries
# as audit, but nobody is paged today (Phase 6 closeout note). This file
# closes that gap.
#
# Prereq: `var.alert_notification_channel_id` is the resource id of a
# pre-existing email or PagerDuty notification channel. Channels are
# created out-of-band (Cloud Console > Monitoring > Alerting > Edit
# Notification Channels) because email channels require interactive
# verification. Pass the full id, e.g.
# "projects/ps2o-surplusas-api/notificationChannels/12345".

# ---------------------------------------------------------------------------
# Log-based metric: count sweeps where dead_lettered > 0.
# ---------------------------------------------------------------------------
resource "google_logging_metric" "webhook_dead_lettered" {
  project     = var.project_id
  name        = "webhook_retry_dead_lettered"
  description = "Counts retry sweeps that dead-lettered at least one delivery (attempt=5, never delivered)."

  # structlog renders the event name as `event` and the summary keys as
  # top-level fields; Cloud Logging exposes them under jsonPayload.
  filter = <<-EOT
    jsonPayload.event="webhook_retry_sweep"
    jsonPayload.dead_lettered>=1
    resource.type="cloud_run_revision"
  EOT

  metric_descriptor {
    metric_kind  = "DELTA"
    value_type   = "INT64"
    unit         = "1"
    display_name = "Webhook deliveries dead-lettered"
  }

  # Sum dead_lettered values across log entries so a single sweep that
  # dead-letters N rows contributes N to the metric — not just 1.
  value_extractor = "EXTRACT(jsonPayload.dead_lettered)"

  label_extractors = {}
}

# ---------------------------------------------------------------------------
# Alert policy: page on first occurrence within a 5-minute window.
# ---------------------------------------------------------------------------
resource "google_monitoring_alert_policy" "webhook_dead_lettered" {
  project      = var.project_id
  display_name = "Webhook delivery dead-lettered"
  combiner     = "OR"
  severity     = "WARNING"

  documentation {
    content   = <<-EOT
      A webhook delivery reached attempt=5 with `delivered_at IS NULL`. The
      delivery is no longer retried automatically. Investigate with:

      ```sql
      SELECT delivery_id, event_type, attempt, last_status_code,
             last_error, created_at
      FROM agents.webhook_deliveries
      WHERE delivered_at IS NULL AND attempt >= 5
      ORDER BY created_at DESC;
      ```

      See `CLAUDE.md` "Webhook semantics" and `Phase6Close.md` for the
      retry contract.
    EOT
    mime_type = "text/markdown"
  }

  conditions {
    display_name = "Any dead-letter event in 5m"

    condition_threshold {
      filter          = "metric.type=\"logging.googleapis.com/user/${google_logging_metric.webhook_dead_lettered.name}\" resource.type=\"cloud_run_revision\""
      comparison      = "COMPARISON_GT"
      threshold_value = 0
      duration        = "0s"

      aggregations {
        alignment_period   = "300s"
        per_series_aligner = "ALIGN_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  notification_channels = [var.alert_notification_channel_id]

  alert_strategy {
    auto_close = "604800s" # 7 days
  }
}
