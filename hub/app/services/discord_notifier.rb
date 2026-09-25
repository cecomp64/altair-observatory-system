# Posts a simple message to a Discord webhook. Used for per-user
# progress alerts (User#discord_webhook_url), falling back to a
# system-wide webhook (ENV["DISCORD_DEFAULT_WEBHOOK_URL"]) when the user
# hasn't configured their own.
class DiscordNotifier
  def self.notify(webhook_url:, content:)
    new(webhook_url).notify(content)
  end

  def initialize(webhook_url)
    @webhook_url = webhook_url
  end

  def notify(content)
    return false if webhook_url.blank?

    response = Faraday.post(webhook_url) do |req|
      req.headers["Content-Type"] = "application/json"
      req.body = { content: content }.to_json
    end

    response.success?
  rescue Faraday::Error => e
    Rails.logger.error("DiscordNotifier failed: #{e.message}")
    false
  end

  private

  attr_reader :webhook_url
end
