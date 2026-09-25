require "rails_helper"

RSpec.describe NotifyOwnerJob, type: :job do
  let(:user) { create(:user, notify_email: true, notify_discord: false) }
  let(:target) { create(:target, user: user) }
  let(:event) { create(:target_event, target: target, event_type: :progress) }

  it "emails the owner when they've opted into email notifications" do
    expect { NotifyOwnerJob.perform_now(event.id) }
      .to have_enqueued_mail(UserMailer, :target_progress)
  end

  it "does not email when the owner has opted out" do
    user.update!(notify_email: false)

    expect { NotifyOwnerJob.perform_now(event.id) }
      .not_to have_enqueued_mail(UserMailer, :target_progress)
  end

  it "posts to Discord when opted in and a webhook is configured" do
    user.update!(notify_discord: true, discord_webhook_url: "https://discord.com/api/webhooks/x/y")

    expect(DiscordNotifier).to receive(:notify).with(hash_including(webhook_url: "https://discord.com/api/webhooks/x/y"))

    NotifyOwnerJob.perform_now(event.id)
  end
end
