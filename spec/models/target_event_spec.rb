require "rails_helper"

RSpec.describe TargetEvent, type: :model do
  it { is_expected.to belong_to(:target) }

  it "enqueues a NotifyOwnerJob after creation" do
    target = create(:target)

    expect {
      target.target_events.create!(event_type: :progress, payload: {})
    }.to have_enqueued_job(NotifyOwnerJob)
  end
end
