# Tells the admins about a processing issue (or a stale node): email to every
# admin, and the system Discord webhook when configured.
class AdminAlertJob < ApplicationJob
  queue_as :default

  def perform(issue_id = nil, change = "opened", node_id: nil)
    subject, body, url = issue_id ? issue_message(issue_id, change) : node_message(node_id)
    return unless subject

    User.admin.find_each { |admin| AdminMailer.alert(admin, subject: subject, body: body, url: url).deliver_later }
    webhook = ENV["DISCORD_SYSTEM_WEBHOOK_URL"].presence || ENV["DISCORD_DEFAULT_WEBHOOK_URL"]
    DiscordNotifier.notify(webhook_url: webhook, content: "**#{subject}** — #{body}") if webhook.present?
  end

  private

  def url_helpers = Rails.application.routes.url_helpers

  def issue_message(id, change)
    issue = ProcessingIssue.find_by(id: id)
    return unless issue

    verb = change == "opened" ? "opened" : issue.status
    [ "#{issue.kind} #{verb} (#{issue.severity})", issue.message, url_helpers.issue_url(issue, **mailer_host) ]
  end

  def node_message(id)
    node = ProcessingNode.find_by(id: id)
    return unless node

    last = node.last_heartbeat_at ? "last heartbeat #{node.last_heartbeat_at.utc.iso8601}" : "never sent a heartbeat"
    [ "Processing node #{node.name} is silent", "No heartbeat for 30 minutes (#{last}).", url_helpers.admin_processing_node_url(node, **mailer_host) ]
  end

  def mailer_host
    Rails.application.config.action_mailer.default_url_options || { host: "localhost" }
  end
end
