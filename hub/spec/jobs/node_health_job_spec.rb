require "rails_helper"

RSpec.describe NodeHealthJob do
  it "alerts admins once per silence" do
    node = create(:processing_node, last_heartbeat_at: 2.hours.ago)
    create(:processing_node, last_heartbeat_at: 1.minute.ago)

    expect { described_class.perform_now }.to have_enqueued_job(AdminAlertJob).with(nil, node_id: node.id)
    expect { described_class.perform_now }.not_to have_enqueued_job(AdminAlertJob)

    node.update!(last_heartbeat_at: 1.minute.ago)
    travel 2.hours do
      expect { described_class.perform_now }.to have_enqueued_job(AdminAlertJob)
    end
  end
end

RSpec.describe AdminAlertJob do
  it "emails every admin about an issue" do
    create(:user, :admin)
    create(:user, :admin)
    issue = ProcessingIssue.create!(processing_node: create(:processing_node), fingerprint: "NAS_SPACE_LOW", kind: "NAS_SPACE_LOW",
                                    severity: "warning", message: "NAS 8% free", opened_at: Time.current)
    expect { described_class.perform_now(issue.id, "opened") }.to have_enqueued_mail(AdminMailer, :alert).twice
  end
end
