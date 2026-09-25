class NotifyOwnerJob < ApplicationJob
  queue_as :default

  def perform(target_event_id)
    event = TargetEvent.find_by(id: target_event_id)
    return unless event

    user = event.target.user

    UserMailer.target_progress(event).deliver_later if user.notify_email?

    if user.notify_discord?
      webhook_url = user.discord_webhook_url.presence || ENV["DISCORD_DEFAULT_WEBHOOK_URL"]
      DiscordNotifier.notify(webhook_url: webhook_url, content: discord_message(event)) if webhook_url.present?
    end
  end

  private

  def discord_message(event)
    target = event.target
    "**#{target.name}** (#{target.telescope.name}): #{event.summary} — #{target.percent_complete}% complete"
  end
end
