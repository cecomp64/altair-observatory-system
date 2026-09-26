require "rails_helper"

# Pages re-render live (Turbo morph refreshes) when Altair reports changes.
RSpec.describe "Live updates", type: :model do
  include ActiveJob::TestHelper

  let(:target) { create(:target) }
  let(:node) { create(:processing_node) }

  def refresh_streams
    enqueued_jobs.select { |j| j["job_class"] == "Turbo::Streams::BroadcastStreamJob" }
                 .map { |j| j["arguments"].first }
  end

  it "refreshes the target and project pages when a master arrives" do
    create(:data_product, target: target, kind: :night_master)
    expect(refresh_streams).to include(target.to_gid_param, target.project.to_gid_param)
  end

  it "refreshes the issue lists, the issue and its project when an issue changes" do
    issue = ProcessingIssue.create!(processing_node: node, fingerprint: "f1", kind: "FLAT_MISSING", severity: "blocking",
                                    status: "open", message: "No SII flat", project: target.project, target: target, opened_at: Time.current)
    expect(refresh_streams).to include("processing_issues", target.project.to_gid_param)

    clear_enqueued_jobs
    issue.update!(status: "resolved", resolved_at: Time.current)
    expect(refresh_streams).to include("processing_issues", issue.to_gid_param, target.project.to_gid_param)
  end
end
